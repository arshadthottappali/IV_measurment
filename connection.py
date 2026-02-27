import pyvisa
import re
import math
import logging


logger = logging.getLogger(__name__)


class KeithleyConnection:
    MAX_ABS_VOLTAGE = 210.0
    MAX_COMPLIANCE_UA = 1_000_000.0

    def __init__(self):
        self.rm = None
        self.inst = None
        self.idn = ""
        self.mode = "scpi"
        self.channel = "smua"
        self.last_resource = ""
        self.output_enabled = False

    def initialize(self):
        if self.rm is None:
            try:
                self.rm = pyvisa.ResourceManager()
                logger.info("VISA ResourceManager initialized")
            except Exception:
                logger.exception("Failed to initialize VISA ResourceManager")
                raise

    def list_devices(self):
        self.initialize()
        try:
            resources = list(self.rm.list_resources())
            logger.info("Detected %d VISA resources", len(resources))
            return resources
        except Exception:
            logger.exception("Failed to list VISA resources")
            raise

    def connect(self, resource_name: str):
        self.initialize()
        logger.info("Connecting to resource: %s", resource_name)
        if self.inst:
            try:
                self.zero_output()
            except Exception:
                logger.exception("Failed to zero output before reconnect")
                pass
            try:
                self.inst.close()
            except Exception:
                logger.exception("Failed to close previous instrument session")
                pass
        self.inst = self._open_resource_with_gpib_fallback(resource_name)
        self.last_resource = getattr(self.inst, "resource_name", resource_name)
        self.inst.timeout = 5000
        self.inst.write_termination = "\n"
        self.inst.read_termination = "\n"

        try:
            self.idn = self._query_id()
            self._validate_instrument_identity()
            self.mode = "tsp2600" if "2602" in self.idn.upper() else "scpi"
            if self.mode == "tsp2600":
                self._setup_2600_defaults()
            else:
                self._setup_scpi_defaults()
            self.output_enabled = False
            logger.info("Connected to %s using mode %s", self.last_resource, self.mode)
            return self.idn
        except Exception:
            logger.exception("Connection setup failed for resource %s", resource_name)
            self._safe_close_instrument()
            raise

    def set_voltage(self, voltage: float):
        self._require_connection()
        self._validate_voltage(voltage)
        try:
            self.enable_output()
            if self.mode == "tsp2600":
                self._write(f"{self.channel}.source.levelv = {voltage}")
            else:
                self._write(f"SOUR:VOLT {voltage}")
            self._check_instrument_errors()
            logger.debug("Voltage set to %s V", voltage)
        except Exception:
            logger.exception("Failed setting voltage to %s V", voltage)
            raise

    def set_current_compliance_ua(self, compliance_ua: float):
        self._require_connection()
        self._validate_compliance(compliance_ua)
        compliance_a = compliance_ua * 1e-6
        try:
            if self.mode == "tsp2600":
                self._write(f"{self.channel}.source.limiti = {compliance_a}")
            else:
                self._write(f"SENS:CURR:PROT {compliance_a}")
            self._check_instrument_errors()
            logger.debug("Compliance set to %s uA", compliance_ua)
        except Exception:
            logger.exception("Failed setting compliance to %s uA", compliance_ua)
            raise

    def measure_current(self) -> float:
        self._require_connection()
        try:
            if self.mode == "tsp2600":
                raw = self._query(f"print({self.channel}.measure.i())")
            else:
                raw = self._query("MEAS:CURR?")
            value = self._extract_first_float(raw)
            # Keithley overflow convention for invalid/overrange values.
            if abs(value) >= 9.9e37:
                raise RuntimeError(
                    "Current reading is overrange/compliance (instrument returned overflow). "
                    "Reduce voltage or increase current compliance/range."
                )
            return value
        except Exception:
            logger.exception("Current measurement failed")
            raise

    def run_tsp_sweep(self, voltages, delay_s: float):
        self._require_connection()
        if self.mode != "tsp2600":
            raise RuntimeError("Fast instrument sweep is available only for Keithley 2600 TSP mode")
        if not voltages:
            return []
        if not isinstance(delay_s, (int, float)) or not math.isfinite(delay_s) or delay_s < 0:
            raise RuntimeError("Delay must be a non-negative finite number")

        for v in voltages:
            self._validate_voltage(v)

        original_timeout = getattr(self.inst, "timeout", 5000)
        estimated_ms = int(max(20_000, min(300_000, (len(voltages) * max(delay_s, 1e-6) * 1000 * 8))))
        self.inst.timeout = max(original_timeout, estimated_ms)
        logger.info(
            "Fast TSP sweep timeout set to %d ms (original %d ms, points=%d, delay=%s s)",
            self.inst.timeout,
            original_timeout,
            len(voltages),
            delay_s,
        )
        try:
            self.enable_output()
            points = ",".join(f"{float(v):.12g}" for v in voltages)
            # Execute full loop on instrument to avoid host-side timing jitter.
            cmd = (
                f"local pts={{ {points} }}; "
                f"local out=''; "
                f"for idx,v in ipairs(pts) do "
                f"{self.channel}.source.levelv=v; "
                f"delay({delay_s:.9g}); "
                f"local i={self.channel}.measure.i(); "
                f"out=out..string.format('%.12g,%.12e;', v, i); "
                f"end; "
                f"print(out)"
            )
            raw = self._query(cmd).strip()
            pairs = [p for p in raw.split(";") if p]
            result = []
            for pair in pairs:
                try:
                    sv, si = pair.split(",", 1)
                    result.append((float(sv), float(si)))
                except Exception:
                    continue
            if not result:
                raise RuntimeError("Instrument fast sweep returned no parseable data")
            self._check_instrument_errors()
            return result
        except Exception:
            logger.exception("Fast TSP sweep failed")
            raise
        finally:
            try:
                self.inst.timeout = original_timeout
                logger.info("Fast TSP sweep timeout restored to %d ms", original_timeout)
            except Exception:
                logger.exception("Failed restoring VISA timeout after fast sweep")

    def enable_output(self):
        self._require_connection()
        try:
            if self.mode == "tsp2600":
                self._write(f"{self.channel}.source.output = {self.channel}.OUTPUT_ON")
            else:
                self._write("OUTP ON")
            self.output_enabled = True
            self._check_instrument_errors()
        except Exception:
            logger.exception("Failed to enable output")
            raise

    def disable_output(self):
        self._require_connection()
        try:
            if self.mode == "tsp2600":
                self._write(f"{self.channel}.source.output = {self.channel}.OUTPUT_OFF")
            else:
                self._write("OUTP OFF")
            self.output_enabled = False
            self._check_instrument_errors()
        except Exception:
            logger.exception("Failed to disable output")
            raise

    def zero_output(self):
        if self.inst:
            try:
                if self.mode == "tsp2600":
                    self._write(f"{self.channel}.source.levelv = 0")
                else:
                    self._write("SOUR:VOLT 0")
            finally:
                try:
                    self.disable_output()
                except Exception:
                    self.output_enabled = False

    def close(self):
        logger.info("Closing connection for resource: %s", self.last_resource or "unknown")
        try:
            self.zero_output()
        except Exception:
            logger.exception("Failed to safely zero output during close")
            pass
        if self.inst:
            try:
                self.inst.close()
            except Exception:
                logger.exception("Failed to close instrument session")
                pass
            self.inst = None
        if self.rm:
            try:
                self.rm.close()
            except Exception:
                logger.exception("Failed to close VISA ResourceManager")
                pass
            self.rm = None

    def _require_connection(self):
        if not self.inst:
            raise RuntimeError("Not connected")

    def _query_id(self):
        try:
            return self._query("*IDN?").strip()
        except Exception:
            try:
                model = self._query("print(localnode.model)").strip()
                return f"KEITHLEY,{model},TSP"
            except Exception:
                return "Unknown instrument"

    def _validate_instrument_identity(self):
        id_upper = self.idn.upper()
        if "KEITHLEY" not in id_upper and "TEKTRONIX" not in id_upper:
            raise RuntimeError(
                f"Connected device does not look like a supported Keithley SMU: {self.idn}"
            )

    def _setup_scpi_defaults(self):
        self._write("*CLS")
        self._write("OUTP OFF")
        self._write("SOUR:VOLT 0")
        self._write("SENS:CURR:RANG:AUTO ON")
        self._write("SENS:CURR:PROT 1E-6")
        self._check_instrument_errors()

    def _setup_2600_defaults(self):
        self._write(f"{self.channel}.reset()")
        self._write(f"{self.channel}.source.func = {self.channel}.OUTPUT_DCVOLTS")
        self._write(f"{self.channel}.source.levelv = 0")
        self._write(f"{self.channel}.source.limiti = 1e-6")
        self._write(f"{self.channel}.measure.autorangei = {self.channel}.AUTORANGE_ON")
        self._write(f"{self.channel}.source.output = {self.channel}.OUTPUT_OFF")
        self._check_instrument_errors()

    @staticmethod
    def _extract_first_float(raw: str) -> float:
        first = raw.strip().split(",")[0]
        match = re.search(r"[+-]?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?", first)
        if not match:
            raise RuntimeError(f"Unexpected current response: {raw.strip()}")
        return float(match.group(0))

    def _check_instrument_errors(self):
        if not self.inst:
            return
        if self.mode == "tsp2600":
            count_raw = self._query("print(errorqueue.count)").strip()
            count = int(abs(self._extract_first_float(count_raw)))
            if count <= 0:
                return
            errors = []
            for _ in range(count):
                raw = self._query(
                    "code, msg, sev, node = errorqueue.next(); "
                    "print(code .. '|' .. msg .. '|' .. sev .. '|' .. node)"
                ).strip()
                parts = raw.split("|")
                code = int(self._extract_first_float(parts[0])) if parts else 0
                if code != 0:
                    msg = parts[1].strip() if len(parts) > 1 else raw
                    errors.append(f"{code}: {msg}")
            if errors:
                raise RuntimeError("Instrument error(s): " + "; ".join(errors))
            return
        err = self._query("SYST:ERR?").strip()
        if not err.startswith("0") and not err.startswith("+0"):
            raise RuntimeError(f"Instrument error: {err}")

    def _safe_close_instrument(self):
        if self.inst:
            try:
                self.inst.close()
            except Exception:
                pass
            self.inst = None
            self.output_enabled = False

    def _open_resource_with_gpib_fallback(self, resource_name: str):
        candidates = [resource_name]
        gpib_match = re.match(r"^GPIB(\d+)::(\d+)::INSTR$", resource_name, re.IGNORECASE)
        if gpib_match:
            bus = int(gpib_match.group(1))
            addr = gpib_match.group(2)
            # Common case: adapter index changed between GPIB0 and GPIB1.
            if bus == 0:
                candidates.append(f"GPIB1::{addr}::INSTR")
            elif bus == 1:
                candidates.append(f"GPIB0::{addr}::INSTR")

        errors = []
        for candidate in candidates:
            try:
                return self.rm.open_resource(candidate)
            except Exception as e:
                errors.append((candidate, e))
                if not self._is_resource_not_present_error(e):
                    raise RuntimeError(f"Error opening resource {candidate}: {e}")

        tried = ", ".join(c for c, _ in errors) if errors else resource_name
        last_error = errors[-1][1] if errors else "Unknown error"
        raise RuntimeError(
            "Could not open GPIB resource. Tried: "
            f"{tried}. Last VISA error: {last_error}. "
            "Check instrument GPIB address and whether adapter is GPIB0 or GPIB1."
        )

    @staticmethod
    def _is_resource_not_present_error(error: Exception) -> bool:
        msg = str(error)
        return ("0xBFFF0011" in msg) or ("Insufficient location information" in msg)

    def _write(self, cmd: str):
        try:
            self.inst.write(cmd)
        except Exception:
            logger.exception(
                "VISA write failed [resource=%s mode=%s cmd=%s]",
                self.last_resource or "unknown",
                self.mode,
                cmd,
            )
            raise

    def _query(self, cmd: str):
        try:
            return self.inst.query(cmd)
        except Exception:
            logger.exception(
                "VISA query failed [resource=%s mode=%s cmd=%s]",
                self.last_resource or "unknown",
                self.mode,
                cmd,
            )
            raise

    @classmethod
    def _validate_voltage(cls, voltage: float):
        if not isinstance(voltage, (int, float)) or not math.isfinite(voltage):
            raise RuntimeError("Voltage must be a finite number")
        if abs(voltage) > cls.MAX_ABS_VOLTAGE:
            raise RuntimeError(f"Voltage exceeds allowed range (+/-{cls.MAX_ABS_VOLTAGE:g} V)")

    @classmethod
    def _validate_compliance(cls, compliance_ua: float):
        if not isinstance(compliance_ua, (int, float)) or not math.isfinite(compliance_ua):
            raise RuntimeError("Compliance must be a finite number")
        if compliance_ua <= 0:
            raise RuntimeError("Compliance must be greater than 0 uA")
        if compliance_ua > cls.MAX_COMPLIANCE_UA:
            raise RuntimeError(
                f"Compliance exceeds allowed range ({cls.MAX_COMPLIANCE_UA:g} uA max)"
            )
