from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import csv


@dataclass
class Measurement:
    timestamp: str
    voltage: float
    current: float
    sample_name: str = ""
    operator: str = ""
    notes: str = ""


class DataLogger:
    def __init__(self):
        self.rows = []
        self.output_file = None
        self.sample_name = ""
        self.operator = ""
        self.notes = ""

    def add(self, voltage: float, current: float, auto_save: bool = False):
        measurement = Measurement(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            voltage=voltage,
            current=current,
            sample_name=self.sample_name,
            operator=self.operator,
            notes=self.notes,
        )
        self.rows.append(measurement)
        if auto_save:
            if not self.output_file:
                raise RuntimeError("Output file is not set for auto-save")
            self._append_row(measurement)

    def set_metadata(self, sample_name: str, operator: str, notes: str):
        self.sample_name = sample_name.strip()
        self.operator = operator.strip()
        self.notes = notes.strip()

    def set_output_file(self, file_path: str, reset_file: bool = False):
        self.output_file = Path(file_path)
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        if reset_file:
            with self.output_file.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "voltage", "current", "sample_name", "operator", "notes"])

    def clear(self):
        self.rows.clear()

    def save_csv(self, file_path: str = ""):
        path = Path(file_path) if file_path else self.output_file
        if not path:
            raise RuntimeError("No output file selected")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "voltage", "current", "sample_name", "operator", "notes"])
            for row in self.rows:
                writer.writerow(
                    [row.timestamp, row.voltage, row.current, row.sample_name, row.operator, row.notes]
                )

    def load_csv(self, file_path: str):
        path = Path(file_path)
        if not path.exists():
            raise RuntimeError(f"File not found: {file_path}")

        with path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise RuntimeError("CSV file has no header")

            required = {"timestamp", "voltage", "current"}
            if not required.issubset(set(reader.fieldnames)):
                raise RuntimeError(
                    "CSV must contain at least: timestamp, voltage, current"
                )

            loaded = []
            for row in reader:
                try:
                    loaded.append(
                        Measurement(
                            timestamp=(row.get("timestamp") or "").strip(),
                            voltage=float((row.get("voltage") or "").strip()),
                            current=float((row.get("current") or "").strip()),
                            sample_name=(row.get("sample_name") or "").strip(),
                            operator=(row.get("operator") or "").strip(),
                            notes=(row.get("notes") or "").strip(),
                        )
                    )
                except Exception:
                    continue

        if not loaded:
            raise RuntimeError("No valid measurement rows found in selected CSV")

        self.rows = loaded
        self.output_file = path

    def _append_row(self, row: Measurement):
        file_missing_or_empty = (not self.output_file.exists()) or self.output_file.stat().st_size == 0
        with self.output_file.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if file_missing_or_empty:
                writer.writerow(["timestamp", "voltage", "current", "sample_name", "operator", "notes"])
            writer.writerow([row.timestamp, row.voltage, row.current, row.sample_name, row.operator, row.notes])

