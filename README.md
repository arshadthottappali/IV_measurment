# IV Measurement App (Keithley)

Simple desktop app to control a Keithley source meter for I-V measurements.

## Features
- Detect and connect VISA/GPIB instruments
- Set voltage and current compliance
- Measure current manually or live
- Run one-way or simple cycle sweep (`0 -> +V -> 0 -> -V -> 0`)
- Auto-save sweep data to CSV
- Load previous CSV and plot
- Linear/Log axis selection for plot

## Project Files
- `main.py` - app entry point
- `gui.py` - Tkinter UI logic
- `connection.py` - instrument communication
- `data_logging.py` - CSV logging/load
- `plotting.py` - popup plotting

## Requirements
- Python 3.10+ (tested with newer versions)
- `pyvisa`
- `matplotlib`
- VISA backend installed (NI-VISA or compatible)
- Keithley interface driver (for GPIB adapter like KUSB-488A)

## Run
```powershell
python main.py
```

If using virtual environment:
```powershell
venv\Scripts\python.exe main.py
```

## Notes
- Set safe compliance and voltage before sweep.
- App enforces software limits and shows safety warnings.
- Backend logs are written to `keithley_backend.log`.
