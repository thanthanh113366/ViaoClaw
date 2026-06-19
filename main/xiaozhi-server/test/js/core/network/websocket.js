// WebSocket message handling module
import { getConfig, saveConnectionUrls } from '../../config/manager.js?v=0205';
import { uiController } from '../../ui/controller.js?v=0205';
import { log } from '../../utils/logger.js?v=0205';
import { getAudioPlayer } from '../audio/player.js?v=0205';
import { getAudioRecorder } from '../audio/recorder.js?v=0205';
import { executeMcpTool, getMcpTools, setWebSocket as setMcpWebSocket } from '../mcp/tools.js?v=0205';
import { webSocketConnect } from './ota-connector.js?v=0205';

// WebSocket handler class
export class WebSocketHandler {
    constructor() {
        this.websocket = null;
        this.onConnectionStateChange = null;
        this.onRecordButtonStateChange = null;
        this.onSessionStateChange = null;
        this.onSessionEmotionChange = null;
        this.onChatMessage = null; // Chat message callback
        this.currentSessionId = null;
        this.isRemoteSpeaking = false;
    }

    // Send hello handshake message
    async sendHelloMessage() {
        if (!this.websocket || this.websocket.readyState !== WebSocket.OPEN) return false;

        try {
            const config = getConfig();

            const helloMessage = {
                type: 'hello',
                device_id: config.deviceId,
                device_name: config.deviceName,
                device_mac: config.deviceMac,
                token: config.token,
                features: {
                    mcp: true
                }
            };

            log('Sending hello handshake', 'info');
            this.websocket.send(JSON.stringify(helloMessage));

            return new Promise(resolve => {
                const timeout = setTimeout(() => {
                    log('Hello response timeout', 'error');
                    log('Hint: try clicking the "Test Auth" button to debug connection', 'info');
                    resolve(false);
                }, 5000);

                const onMessageHandler = (event) => {
                    try {
                        const response = JSON.parse(event.data);
                        if (response.type === 'hello' && response.session_id) {
                            log(`Server handshake successful, session ID: ${response.session_id}`, 'success');
                            clearTimeout(timeout);
                            this.websocket.removeEventListener('message', onMessageHandler);
                            resolve(true);
                        }
                    } catch (e) {
                        // Ignore non-JSON messages
                    }
                };

                this.websocket.addEventListener('message', onMessageHandler);
            });
        } catch (error) {
            log(`Hello message send error: ${error.message}`, 'error');
            return false;
        }
    }

    // Handle text message
    handleTextMessage(message) {
        if (message.type === 'hello') {
            log(`Server response: ${JSON.stringify(message, null, 2)}`, 'success');
            window.cameraAvailable = true;
            log('Connected, camera now available', 'success');
            uiController.updateDialButton(true);
            uiController.startAIChatSession();
        } else if (message.type === 'tts') {
            this.handleTTSMessage(message);
        } else if (message.type === 'audio') {
            log(`Received audio control message: ${JSON.stringify(message)}`, 'info');
        } else if (message.type === 'stt') {
            log(`Recognition result: ${message.text}`, 'info');
            // Check if device binding is needed
            if (message.text && (message.text.includes('binding') || message.text.includes('bind'))) {
                log('Received device bind prompt, updating camera state', 'warning');
                window.cameraAvailable = false;
                // Close camera
                if (typeof window.stopCamera === 'function') {
                    window.stopCamera();
                }
                // Update camera button state
                const cameraBtn = document.getElementById('cameraBtn');
                if (cameraBtn) {
                    cameraBtn.classList.remove('camera-active');
                    cameraBtn.querySelector('.btn-text').textContent = 'Camera';
                    cameraBtn.disabled = true;
                    cameraBtn.title = 'Please bind verification code first';
                }
            }
            // Use new chat message callback to display STT message
            if (this.onChatMessage && message.text) {
                this.onChatMessage(message.text, true);
            }
        } else if (message.type === 'llm') {
            log(`LLM reply: ${message.text}`, 'info');
            // Use new chat message callback to display LLM reply
            if (this.onChatMessage && message.text) {
                this.onChatMessage(message.text, false);
            }

            // If contains emotion, update sessionStatus and trigger Live2D motion
            if (message.text && /[\u{1F300}-\u{1F9FF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]/u.test(message.text)) {
                // Extract emotion symbol
                const emojiMatch = message.text.match(/[\u{1F300}-\u{1F9FF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]/u);
                if (emojiMatch && this.onSessionEmotionChange) {
                    this.onSessionEmotionChange(emojiMatch[0]);
                }

                // Trigger Live2D emotion motion
                if (message.emotion) {
                    console.log(`Received emotion message: emotion=${message.emotion}, text=${message.text}`);
                    this.triggerLive2DEmotionAction(message.emotion);
                }
            }

            // Only add to conversation if text is more than just an emotion
            // Remove emotion from text and check if content remains
            const textWithoutEmoji = message.text ? message.text.replace(/[\u{1F300}-\u{1F9FF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]/gu, '').trim() : '';
            if (textWithoutEmoji && this.onChatMessage) {
                this.onChatMessage(message.text, false);
            }
        } else if (message.type === 'mcp') {
            this.handleMCPMessage(message);
        } else if (message.type === 'vad') {
            this.handleVADMessage(message);
        } else {
            log(`Unknown message type: ${message.type}`, 'info');
            if (this.onChatMessage) {
                this.onChatMessage(`Unknown message type: ${message.type}\n${JSON.stringify(message, null, 2)}`, false);
            }
        }
    }

    // Handle VAD message (voice activity detection state from server)
    handleVADMessage(message) {
        if (message.state === 'speech_start') {
            log('VAD: speech detected', 'info');
            if (this.onRecordButtonStateChange) {
                this.onRecordButtonStateChange('listening');
            }
        } else if (message.state === 'speech_stop') {
            log(`VAD: speech stopped (${message.reason || 'silence'})`, 'info');
        }
    }

    // Handle TTS message
    handleTTSMessage(message) {
        if (message.state === 'start') {
            log('Server started sending audio', 'info');
            this.currentSessionId = message.session_id;
            this.isRemoteSpeaking = true;
            if (this.onSessionStateChange) {
                this.onSessionStateChange(true);
            }

            // Start Live2D speaking animation
            this.startLive2DTalking();
        } else if (message.state === 'sentence_start') {
            log(`Server sending audio segment: ${message.text}`, 'info');
            this.ttsSentenceCount = (this.ttsSentenceCount || 0) + 1;

            if (message.text && this.onChatMessage) {
                this.onChatMessage(message.text, false);
            }

            // Ensure animation runs at sentence start
            const live2dManager = window.chatApp?.live2dManager;
            if (live2dManager && !live2dManager.isTalking) {
                this.startLive2DTalking();
            }
        } else if (message.state === 'sentence_end') {
            log(`Audio segment ended: ${message.text}`, 'info');

            // Do not clear animation at sentence end, wait for next or final stop
        } else if (message.state === 'stop') {
            log('Server audio transmission ended, clearing all audio buffers', 'info');

            // Clear all audio buffers and stop playback
            const audioPlayer = getAudioPlayer();
            audioPlayer.clearAllAudio();

            this.isRemoteSpeaking = false;
            if (this.onRecordButtonStateChange) {
                this.onRecordButtonStateChange(false);
            }
            if (this.onSessionStateChange) {
                this.onSessionStateChange(false);
            }

            // Delay stopping Live2D animation to ensure all sentences finish
            setTimeout(() => {
                this.stopLive2DTalking();
                this.ttsSentenceCount = 0; // reset counter
            }, 1000); // 1s delay to ensure all sentences complete
        }
    }

    // Start Live2D speaking animation
    startLive2DTalking() {
        try {
            // Get Live2D manager instance
            const live2dManager = window.chatApp?.live2dManager;
            if (live2dManager && live2dManager.live2dModel) {
                // Use audio player analyser node
                live2dManager.startTalking();
                log('Live2D speaking animation started', 'info');
            }
        } catch (error) {
            log(`Failed to start Live2D speaking animation: ${error.message}`, 'error');
        }
    }

    // Stop Live2D speaking animation
    stopLive2DTalking() {
        try {
            const live2dManager = window.chatApp?.live2dManager;
            if (live2dManager) {
                live2dManager.stopTalking();
                log('Live2D speaking animation stopped', 'info');
            }
        } catch (error) {
            log(`Failed to stop Live2D speaking animation: ${error.message}`, 'error');
        }
    }

    // Initialize Live2D audio analyser
    initializeLive2DAudioAnalyzer() {
        try {
            const live2dManager = window.chatApp?.live2dManager;
            if (live2dManager) {
                // Initialize audio analyser (using audio player context)
                if (live2dManager.initializeAudioAnalyzer()) {
                    log('Live2D audio analyser initialized and connected to audio player', 'success');
                } else {
                    log('Live2D audio analyser init failed, will use simulated animation', 'warning');
                }
            }
        } catch (error) {
            log(`Failed to initialize Live2D audio analyser: ${error.message}`, 'error');
        }
    }

    // Handle MCP message
    handleMCPMessage(message) {
        const payload = message.payload || {};
        log(`Server sent: ${JSON.stringify(message)}`, 'info');

        if (payload.method === 'tools/list') {
            const tools = getMcpTools();

            const replyMessage = JSON.stringify({
                "session_id": message.session_id || "",
                "type": "mcp",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": payload.id,
                    "result": {
                        "tools": tools
                    }
                }
            });
            log(`Client reported: ${replyMessage}`, 'info');
            this.websocket.send(replyMessage);
            log(`Replied with MCP tool list: ${tools.length} tools`, 'info');

        } else if (payload.method === 'tools/call') {
            const toolName = payload.params?.name;
            const toolArgs = payload.params?.arguments;

            log(`Calling tool: ${toolName} args: ${JSON.stringify(toolArgs)}`, 'info');

            executeMcpTool(toolName, toolArgs).then(result => {
                const replyMessage = JSON.stringify({
                    "session_id": message.session_id || "",
                    "type": "mcp",
                    "payload": {
                        "jsonrpc": "2.0",
                        "id": payload.id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": JSON.stringify(result)
                                }
                            ],
                            "isError": false
                        }
                    }
                });

                log(`Client reported: ${replyMessage}`, 'info');
                this.websocket.send(replyMessage);
            }).catch(error => {
                log(`Tool execution failed: ${error.message}`, 'error');
                const errorReply = JSON.stringify({
                    "session_id": message.session_id || "",
                    "type": "mcp",
                    "payload": {
                        "jsonrpc": "2.0",
                        "id": payload.id,
                        "error": {
                            "code": -32603,
                            "message": error.message
                        }
                    }
                });
                this.websocket.send(errorReply);
            });
        } else if (payload.method === 'initialize') {
            log(`Received tool init request: ${JSON.stringify(payload.params)}`, 'info');
            // Save vision analysis API URL
            const visionUrl = document.getElementById('visionUrl');
            const visionConfig = payload?.params?.capabilities?.vision;
            if (visionConfig && typeof visionConfig === 'object' && visionConfig.url && visionConfig.token) {
                const visionConfigStr = JSON.stringify(visionConfig);
                localStorage.setItem('xz_tester_vision', visionConfigStr);
                if (visionUrl) visionUrl.value = visionConfig.url;
            } else {
                localStorage.removeItem('xz_tester_vision');
                if (visionUrl) visionUrl.value = '';
            }

            const replyMessage = JSON.stringify({
                "session_id": message.session_id || "",
                "type": "mcp",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": payload.id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {}
                        },
                        "serverInfo": {
                            "name": "xiaozhi-web-test",
                            "version": "2.1.0"
                        }
                    }
                }
            });
            log(`Replied with init response`, 'info');
            this.websocket.send(replyMessage);
        } else {
            log(`Unknown MCP method: ${payload.method}`, 'warning');
        }
    }

    // Handle binary message
    async handleBinaryMessage(data) {
        try {
            let arrayBuffer;
            if (data instanceof ArrayBuffer) {
                arrayBuffer = data;
            } else if (data instanceof Blob) {
                arrayBuffer = await data.arrayBuffer();
                log(`Received Blob audio data, size: ${arrayBuffer.byteLength} bytes`, 'debug');
            } else {
                log(`Received unknown binary data type: ${typeof data}`, 'warning');
                return;
            }

            const opusData = new Uint8Array(arrayBuffer);
            const audioPlayer = getAudioPlayer();
            audioPlayer.enqueueAudioData(opusData);
        } catch (error) {
            log(`Binary message handling error: ${error.message}`, 'error');
        }
    }

    // Connect to WebSocket server
    async connect() {
        const config = getConfig();
        log('Checking OTA status...', 'info');
        saveConnectionUrls();

        try {
            const otaUrl = document.getElementById('otaUrl').value.trim();
            const ws = await webSocketConnect(otaUrl, config);
            if (ws === undefined) {
                return false;
            }
            this.websocket = ws;

            // Set binary data type to ArrayBuffer
            this.websocket.binaryType = 'arraybuffer';

            // Set WebSocket instance for MCP module
            setMcpWebSocket(this.websocket);

            // Set WebSocket for recorder
            const audioRecorder = getAudioRecorder();
            audioRecorder.setWebSocket(this.websocket);

            this.setupEventHandlers();

            return true;
        } catch (error) {
            log(`Connection error: ${error.message}`, 'error');
            if (this.onConnectionStateChange) {
                this.onConnectionStateChange(false);
            }
            return false;
        }
    }

    // Set up event handlers
    setupEventHandlers() {
        this.websocket.onopen = async () => {
            const url = document.getElementById('serverUrl').value;
            log(`Connected to server: ${url}`, 'success');

            if (this.onConnectionStateChange) {
                this.onConnectionStateChange(true);
            }

            // After connection, default state is listening
            this.isRemoteSpeaking = false;
            if (this.onSessionStateChange) {
                this.onSessionStateChange(false);
            }

            // Initialize Live2D audio analyser on WebSocket connect
            this.initializeLive2DAudioAnalyzer();

            await this.sendHelloMessage();
        };

        this.websocket.onclose = () => {
            log('Disconnected', 'info');

            if (this.onConnectionStateChange) {
                this.onConnectionStateChange(false);
            }

            const audioRecorder = getAudioRecorder();
            audioRecorder.stop();

            // Close camera
            if (typeof window.stopCamera === 'function') {
                window.stopCamera();
            }

            // Hide camera display area
            const cameraContainer = document.getElementById('cameraContainer');
            if (cameraContainer) {
                cameraContainer.classList.remove('active');
            }
        };

        this.websocket.onerror = (error) => {
            log(`WebSocket error: ${error.message || 'unknown error'}`, 'error');
            uiController.addChatMessage(`⚠️ WebSocket error: ${error.message || 'unknown error'}`, false);
            if (this.onConnectionStateChange) {
                this.onConnectionStateChange(false);
            }
        };

        this.websocket.onmessage = (event) => {
            try {
                if (typeof event.data === 'string') {
                    const message = JSON.parse(event.data);
                    this.handleTextMessage(message);
                } else {
                    this.handleBinaryMessage(event.data);
                }
            } catch (error) {
                log(`WebSocket message handling error: ${error.message}`, 'error');
                // No longer using old addMessage function as conversationDiv does not exist
                // Error messages will be shown through other means
            }
        };
    }

    // Disconnect
    disconnect() {
        if (!this.websocket) return;

        this.websocket.close();
        const audioRecorder = getAudioRecorder();
        audioRecorder.stop();

        // Close camera
        if (typeof window.stopCamera === 'function') {
            window.stopCamera();
        }

        // Hide camera display area
        const cameraContainer = document.getElementById('cameraContainer');
        if (cameraContainer) {
            cameraContainer.classList.remove('active');
        }
    }

    // Send text message
    sendTextMessage(text) {
        if (text === '' || !this.websocket || this.websocket.readyState !== WebSocket.OPEN) {
            return false;
        }

        try {
            // If server is speaking, send interrupt first
            if (this.isRemoteSpeaking && this.currentSessionId) {
                const abortMessage = {
                    session_id: this.currentSessionId,
                    type: 'abort',
                    reason: 'wake_word_detected'
                };
                this.websocket.send(JSON.stringify(abortMessage));
                log('Sending interrupt message', 'info');
            }

            const listenMessage = {
                type: 'listen',
                state: 'detect',
                text: text
            };

            this.websocket.send(JSON.stringify(listenMessage));
            log(`Sending text message: ${text}`, 'info');

            return true;
        } catch (error) {
            log(`Message send error: ${error.message}`, 'error');
            return false;
        }
    }

    /**
     * Trigger Live2D emotion motion
     * @param {string} emotion - emotion name
     */
    triggerLive2DEmotionAction(emotion) {
        try {
            const live2dManager = window.chatApp?.live2dManager;
            if (live2dManager && typeof live2dManager.triggerEmotionAction === 'function') {
                live2dManager.triggerEmotionAction(emotion);
                log(`Triggering Live2D emotion motion: ${emotion}`, 'info');
            } else {
                log(`Cannot trigger Live2D emotion: manager not found or method unavailable`, 'warning');
            }
        } catch (error) {
            log(`Failed to trigger Live2D emotion motion: ${error.message}`, 'error');
        }
    }

    // Get WebSocket instance
    getWebSocket() {
        return this.websocket;
    }

    // Check if connected
    isConnected() {
        return this.websocket && this.websocket.readyState === WebSocket.OPEN;
    }
}

// Create singleton
let wsHandlerInstance = null;

export function getWebSocketHandler() {
    if (!wsHandlerInstance) {
        wsHandlerInstance = new WebSocketHandler();
    }
    return wsHandlerInstance;
}
