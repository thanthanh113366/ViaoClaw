from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging
import requests
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

EVN_TARIFF = [
    (50, 1984),
    (50, 2050),
    (100, 2380),
    (100, 2998),
    (100, 3350),
    (999999, 3460),
]
VAT_RATE = 0.08


def calculate_evn_cost(energy_kwh: float) -> dict:
    remaining = energy_kwh
    total = 0
    for tier_limit, price in EVN_TARIFF:
        if remaining <= 0:
            break
        tier_kwh = min(remaining, tier_limit)
        total += tier_kwh * price
        remaining -= tier_kwh
    vat = total * VAT_RATE
    return {"pre_tax": total, "vat": vat, "total": total + vat}


class ShellyCloudClient:
    def __init__(self, server: str, device_id: str, auth_key: str, channel: int = 0):
        self.server = server.rstrip("/")
        self.device_id = device_id
        self.auth_key = auth_key
        self.channel = channel
        self.timeout = 10
        self.max_retries = 3

    def _request(self, method: str, path: str, body: dict = None) -> dict:
        url = f"{self.server}{path}?auth_key={self.auth_key}"
        headers = {"Content-Type": "application/json"}

        last_error = None
        for attempt in range(self.max_retries):
            try:
                if method == "POST":
                    response = requests.post(url, headers=headers, json=body, timeout=self.timeout)
                else:
                    response = requests.get(url, headers=headers, timeout=self.timeout)

                logger.bind(tag=TAG).info(
                    f"Shelly API: {method} {path} status={response.status_code}"
                )

                if response.status_code == 200:
                    if response.text.strip():
                        return response.json()
                    return {}
                else:
                    last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                    logger.bind(tag=TAG).error(last_error)

            except (requests.ConnectionError, requests.Timeout) as e:
                last_error = str(e)
                logger.bind(tag=TAG).warning(
                    f"Shelly API attempt {attempt + 1}/{self.max_retries} failed: {e}"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)

        raise Exception(f"Shelly API failed after {self.max_retries} attempts: {last_error}")

    def get_status(self) -> dict:
        body = {"ids": [self.device_id], "select": ["status"]}
        resp = self._request("POST", "/v2/devices/api/get", body)

        if not isinstance(resp, list) or not resp:
            raise Exception(f"Unexpected Shelly response: {resp}")

        device = resp[0]
        if not isinstance(device, dict):
            raise Exception(f"Unexpected device type: {type(device)}")

        status = device.get("status", {})
        if not isinstance(status, dict):
            raise Exception(f"Unexpected status type: {type(status)}")

        switch = status.get("switch:0", {})
        if not isinstance(switch, dict):
            raise Exception(f"Unexpected switch type: {type(switch)}")

        temp = switch.get("temperature", {})
        if not isinstance(temp, dict):
            temp = {}

        aenergy = switch.get("aenergy", {})
        if not isinstance(aenergy, dict):
            aenergy = {}

        return {
            "relay_state": "on" if switch.get("output", False) else "off",
            "power_watts": switch.get("apower", 0.0),
            "voltage": switch.get("voltage", 0.0),
            "temperature_celsius": temp.get("tC", 0.0),
            "energy_total_wh": aenergy.get("total", 0.0),
        }

    def turn_on(self) -> bool:
        body = {"id": self.device_id, "channel": self.channel, "on": True}
        self._request("POST", "/v2/devices/api/set/switch", body)
        return True

    def turn_off(self) -> bool:
        body = {"id": self.device_id, "channel": self.channel, "on": False}
        self._request("POST", "/v2/devices/api/set/switch", body)
        return True

    def is_on(self) -> bool:
        status = self.get_status()
        return status["relay_state"] == "on"

    def get_power(self) -> float:
        status = self.get_status()
        return status["power_watts"]

    def get_voltage(self) -> float:
        status = self.get_status()
        return status["voltage"]

    def get_temperature(self) -> float:
        status = self.get_status()
        return status["temperature_celsius"]

    def get_energy(self) -> float:
        status = self.get_status()
        return status["energy_total_wh"]


shelly_cloud_function_desc = {
    "type": "function",
    "function": {
        "name": "shelly_cloud",
        "description": "Điều khiển thiết bị Shelly: bật/tắt relay, xem trạng thái, công suất, điện áp, nhiệt độ, điện năng tích lũy. Không dùng cho lệnh hẹn giờ/tương lai — dùng cron tool thay thế.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["turn_on", "turn_off", "get_status", "get_energy", "get_cost"],
                    "description": "Hành động: turn_on=bật relay, turn_off=tắt relay, get_status=xem trạng thái đầy đủ, get_energy=xem điện năng đã tiêu thụ, get_cost=tính tiền điện theo bảng giá EVN",
                },
                "device": {
                    "type": "string",
                    "description": "Tên thiết bị: light_bedroom (đèn phòng ngủ), light_livingroom (đèn phòng khách), light_restroom (đèn nhà vệ sinh). Bỏ trống = thiết bị mặc định.",
                },
            },
            "required": ["action"],
        },
    },
}


@register_function("shelly_cloud", shelly_cloud_function_desc, ToolType.SYSTEM_CTL)
def shelly_cloud(conn: "ConnectionHandler", action: str = "", device: str = ""):
    try:
        plugins = conn.config.get("plugins", {})
        shelly_config = plugins.get("shelly_cloud", {})

        server = shelly_config.get("server", "")
        auth_key = shelly_config.get("auth_key", "")
        devices = shelly_config.get("devices", {})

        if device and device in devices:
            dev = devices[device]
            device_id = dev.get("device_id", "")
            channel = dev.get("channel", 0)
        else:
            device_id = shelly_config.get("device_id", "")
            channel = 0

        if not all([server, device_id, auth_key]):
            return ActionResponse(
                Action.ERROR,
                "Shelly Cloud chưa được cấu hình. Hãy thêm plugins.shelly_cloud vào data/.config.yaml",
                None,
            )

        client = ShellyCloudClient(server, device_id, auth_key, channel)

        device_label = device if device else "thiết bị"

        if action == "turn_on":
            client.turn_on()
            result = f"Đã bật {device_label}"
        elif action == "turn_off":
            client.turn_off()
            result = f"Đã tắt {device_label}"
        elif action == "get_status":
            status = client.get_status()
            relay = "đang bật" if status["relay_state"] == "on" else "đang tắt"
            result = (
                f"Đèn hiện {relay}. "
                f"Công suất: {status['power_watts']}W. "
                f"Điện áp: {status['voltage']}V. "
                f"Nhiệt độ: {status['temperature_celsius']}°C. "
                f"Tổng điện năng đã tiêu thụ: {status['energy_total_wh']} Wh"
            )
        elif action == "get_energy":
            energy = client.get_energy()
            result = f"Tổng điện năng đã tiêu thụ: {energy} Wh"
        elif action == "get_cost":
            energy_kwh = client.get_energy() / 1000
            cost = calculate_evn_cost(energy_kwh)
            result = (
                f"Điện năng: {energy_kwh:.3f} kWh\n"
                f"Chưa thuế: {cost['pre_tax']:,.0f} đồng\n"
                f"Thuế GTGT (8%): {cost['vat']:,.0f} đồng\n"
                f"Tổng cộng: {cost['total']:,.0f} đồng"
            )
        else:
            return ActionResponse(Action.ERROR, f"Action không hợp lệ: {action}", None)

        logger.bind(tag=TAG).info(f"Shelly Cloud action={action} result={result}")
        return ActionResponse(Action.RESPONSE, None, result)

    except Exception as e:
        error_msg = f"Lỗi Shelly Cloud: {e}"
        logger.bind(tag=TAG).error(error_msg, exc_info=True)
        return ActionResponse(Action.ERROR, error_msg, None)
