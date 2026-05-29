from config.logger import setup_logging
from core.utils.util import check_model_key

TAG = __name__
logger = setup_logging()

HA_DEVICES_PROMPT_MARKER = "[Home Assistant device list]"


def _format_devices_for_prompt(devices) -> str:
    if not devices:
        return ""
    if isinstance(devices, list):
        return "\n".join(str(device) for device in devices)
    return str(devices)


def build_home_assistant_devices_prompt(plugins_config, config_source: str) -> str:
    devices = plugins_config.get(config_source, {}).get("devices", [])
    device_str = _format_devices_for_prompt(devices)
    if not device_str:
        return ""

    return (
        f"\n\n{HA_DEVICES_PROMPT_MARKER}\n"
        "When calling hass_set_state or hass_get_state, you MUST use entity_id exactly "
        "from this list. Do not invent entity IDs (e.g. do not use light.bedroom).\n"
        "Khi điều khiển Home Assistant, bắt buộc dùng đúng entity_id trong danh sách; "
        "không được đoán tên thiết bị.\n"
        "Format: location, device name, entity_id\n"
        f"{device_str}\n"
    )


def append_devices_to_prompt(conn):
    if getattr(conn, "intent_type", None) != "function_call":
        return

    funcs = conn.config["Intent"][conn.config["selected_module"]["Intent"]].get(
        "functions", []
    )
    if "hass_get_state" not in funcs and "hass_set_state" not in funcs:
        return

    if HA_DEVICES_PROMPT_MARKER in (conn.prompt or ""):
        return

    plugins_config = conn.config.get("plugins", {})
    config_source = (
        "home_assistant"
        if plugins_config.get("home_assistant")
        else "hass_get_state"
    )
    device_prompt = build_home_assistant_devices_prompt(plugins_config, config_source)
    if not device_prompt:
        return

    conn.prompt = (conn.prompt or "") + device_prompt
    dialogue = getattr(conn, "dialogue", None)
    if dialogue is not None:
        dialogue.update_system_message(conn.prompt)
    logger.bind(tag=TAG).info("Home Assistant device list appended to system prompt")


def initialize_hass_handler(conn):
    ha_config = {}
    if getattr(conn, "load_function_plugin", True) is False:
        return ha_config

    plugins_config = conn.config.get("plugins", {})
    config_source = (
        "home_assistant" if plugins_config.get("home_assistant") else "hass_get_state"
    )
    if not plugins_config.get(config_source):
        return ha_config

    plugin_config = plugins_config[config_source]
    ha_config["base_url"] = plugin_config.get("base_url")
    ha_config["api_key"] = plugin_config.get("api_key")

    model_key_msg = check_model_key("home_assistant", ha_config.get("api_key"))
    if model_key_msg:
        logger.bind(tag=TAG).error(model_key_msg)

    return ha_config
