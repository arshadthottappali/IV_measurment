from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import csv
import logging
import math


logger = logging.getLogger(__name__)

@dataclass
class Measurement:
    timestamp: str
    voltage: float
    current: float
    sample_name: str = ""
    operator: str = ""
    notes: str = ""
    elapsed_s: float | None = None
    cycle_id: int = 0
    plot_mode: str = "iv"
    run_description: str = ""


class DataLogger:
    def __init__(self):
        self.rows = []
        self.output_file = None
        self.sample_name = ""
        self.operator = ""
        self.notes = ""
        self.run_description = ""
        self._pd_text_existing_count = 0

    def add(self, voltage: float, current: float, auto_save: bool = False):
        measurement = Measurement(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            voltage=voltage,
            current=current,
            sample_name=self.sample_name,
            operator=self.operator,
            notes=self.notes,
            run_description=self.run_description,
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

    def set_run_description(self, description: str):
        self.run_description = description.strip()

    def set_output_file(self, file_path: str, reset_file: bool = False):
        self.output_file = Path(file_path)
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        self._pd_text_existing_count = 0
        if self.output_file.suffix.lower() == ".txt" and not reset_file and self.output_file.exists():
            self._pd_text_existing_count = self._count_existing_pd_text_rows(self.output_file)
        if reset_file:
            self._write_file_header()

    def clear(self):
        self.rows.clear()

    def save_csv(self, file_path: str = ""):
        path = Path(file_path) if file_path else self.output_file
        if not path:
            raise RuntimeError("No output file selected")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() == ".txt":
            original_output_file = self.output_file
            self.output_file = path
            try:
                self._write_pd_text_file()
            finally:
                self.output_file = path if not file_path else original_output_file
            return
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "timestamp",
                    "voltage",
                    "current",
                    "sample_name",
                    "operator",
                    "notes",
                    "elapsed_s",
                    "cycle_id",
                    "plot_mode",
                    "run_description",
                ]
            )
            for row in self.rows:
                writer.writerow(
                    [
                        row.timestamp,
                        row.voltage,
                        row.current,
                        row.sample_name,
                        row.operator,
                        row.notes,
                        "" if row.elapsed_s is None else row.elapsed_s,
                        row.cycle_id,
                        row.plot_mode,
                        row.run_description,
                    ]
                )

    def load_csv(self, file_path: str):
        path = Path(file_path)
        if not path.exists():
            raise RuntimeError(f"File not found: {file_path}")
        if path.suffix.lower() == ".txt":
            self._load_pd_text(path)
            return

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
            for i, row in enumerate(reader, start=2):
                try:
                    loaded.append(
                        Measurement(
                            timestamp=(row.get("timestamp") or "").strip(),
                            voltage=float((row.get("voltage") or "").strip()),
                            current=float((row.get("current") or "").strip()),
                            sample_name=(row.get("sample_name") or "").strip(),
                            operator=(row.get("operator") or "").strip(),
                            notes=(row.get("notes") or "").strip(),
                            elapsed_s=(
                                float((row.get("elapsed_s") or "").strip())
                                if (row.get("elapsed_s") or "").strip()
                                else None
                            ),
                            cycle_id=int((row.get("cycle_id") or "0").strip() or "0"),
                            plot_mode=(row.get("plot_mode") or "iv").strip() or "iv",
                            run_description=(row.get("run_description") or "").strip(),
                        )
                    )
                except (ValueError, TypeError) as e:
                    logger.warning("Skipping malformed row %d in %s: %s", i, path.name, e)
                    continue

        if not loaded:
            raise RuntimeError("No valid measurement rows found in selected CSV")

        self.rows = loaded
        self.output_file = path

    def _append_row(self, row: Measurement):
        file_missing_or_empty = (not self.output_file.exists()) or self.output_file.stat().st_size == 0
        if file_missing_or_empty:
            self._write_file_header()
        if self.output_file.suffix.lower() == ".txt":
            if not (isinstance(row.current, (int, float)) and math.isfinite(row.current)):
                return
            pulse_no = self._pd_text_existing_count + sum(
                1 for item in self.rows
                if item.plot_mode == "pd"
                and isinstance(item.current, (int, float))
                and math.isfinite(item.current)
            )
            with self.output_file.open("a", encoding="utf-8") as f:
                f.write(f"{pulse_no}\t{abs(row.current):.12e}\t{row.voltage:.12g}\n")
            return
        with self.output_file.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    row.timestamp,
                    row.voltage,
                    row.current,
                    row.sample_name,
                    row.operator,
                    row.notes,
                    "" if row.elapsed_s is None else row.elapsed_s,
                    row.cycle_id,
                    row.plot_mode,
                    row.run_description,
                ]
            )

    def _write_file_header(self):
        if self.output_file.suffix.lower() == ".txt":
            with self.output_file.open("w", encoding="utf-8") as f:
                if self.run_description:
                    f.write(f"# {self.run_description}\n")
                else:
                    f.write(f"# date={datetime.now().strftime('%Y-%m-%d')}\n")
                f.write("No_of_pulse\tread_current_A\tread_voltage_V\n")
            return
        with self.output_file.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "timestamp",
                    "voltage",
                    "current",
                    "sample_name",
                    "operator",
                    "notes",
                    "elapsed_s",
                    "cycle_id",
                    "plot_mode",
                    "run_description",
                ]
            )

    def _write_pd_text_file(self):
        self._write_file_header()
        pulse_no = 0
        with self.output_file.open("a", encoding="utf-8") as f:
            for row in self.rows:
                if row.plot_mode != "pd":
                    continue
                if not (isinstance(row.current, (int, float)) and math.isfinite(row.current)):
                    continue
                pulse_no += 1
                f.write(f"{pulse_no}\t{abs(row.current):.12e}\t{row.voltage:.12g}\n")

    @staticmethod
    def _count_existing_pd_text_rows(path: Path):
        count = 0
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or stripped.lower().startswith("no_of_pulse"):
                        continue
                    count += 1
        except Exception:
            logger.exception("Failed counting existing PD text rows in %s", path)
        return count

    def _load_pd_text(self, path: Path):
        loaded = []
        run_description = ""
        with path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("#"):
                    if not run_description:
                        run_description = stripped.lstrip("#").strip()
                    continue
                if stripped.lower().startswith("no_of_pulse"):
                    continue
                parts = stripped.split()
                if len(parts) < 3:
                    logger.warning("Skipping malformed PD text row %d in %s: %s", i, path.name, stripped)
                    continue
                try:
                    pulse_no = int(float(parts[0]))
                    loaded.append(
                        Measurement(
                            timestamp="",
                            voltage=float(parts[2]),
                            current=float(parts[1]),
                            elapsed_s=float(pulse_no),
                            cycle_id=0,
                            plot_mode="pd",
                            run_description=run_description,
                        )
                    )
                except (ValueError, TypeError) as e:
                    logger.warning("Skipping malformed PD text row %d in %s: %s", i, path.name, e)
        if not loaded:
            raise RuntimeError("No valid PD measurement rows found in selected TXT file")
        self.rows = loaded
        self.output_file = path
        self.run_description = run_description
        self._pd_text_existing_count = len(loaded)
