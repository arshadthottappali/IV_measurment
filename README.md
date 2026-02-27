# IV Measurement App (Keithley)

Desktop app for Keithley source-meter control and I-V/WRER measurement workflows.

## Features
- VISA device detection and connection (SCPI/TSP handling in backend)
- Manual voltage apply and current read
- Compliance setting in uA with safety checks
- Live current read mode
- Sweep module with 3 subsections:
  - `Standard Sweep` (One-way / Simple cycle)
  - `Custom Sequence` (user-built multi-segment loops)
  - `WRER` (Write -> Read -> Erase -> Read, constant-voltage time sampling)
- Host timing and Fast TSP execution modes
- CSV auto-save / append / load
- Plotting:
  - I-V plot with cycle colors + legend
  - WRER two-panel plot (Voltage-Time top, Current-Time bottom)
  - Optional log Y (for current panel; uses abs(current) for display)
- UI state persistence between runs (`keithley_ui_settings.json`)

## Project Files
- `main.py` - app entry point + logging setup
- `gui.py` - Tkinter UI and run logic
- `connection.py` - instrument communication + safety checks
- `data_logging.py` - CSV logging/load
- `plotting.py` - popup plotting
- `KeithleyIV.spec` - PyInstaller build spec

## Requirements
- Windows (for current EXE build)
- Python 3.10+ (tested with newer versions)
- `pyvisa`, `matplotlib`
- VISA runtime installed (NI-VISA or compatible)
- Instrument interface driver installed (for example NI-488.2 / KUSB-488A driver)

## Run From Source
```powershell
venv\Scripts\python.exe main.py
```

or
```powershell
python main.py
```

## Run EXE
- Use: `dist\KeithleyIV\KeithleyIV.exe`
- Keep `_internal` folder in the same directory as the exe.
- On first run, Windows SmartScreen may warn because exe is unsigned.

## Basic Workflow
1. `Setup` tab:
   - Click `Detect Devices`
   - Select resource (for example `GPIB0::26::INSTR`)
   - Click `Connect`
2. `Control` tab:
   - Set compliance (`uA`) and click `Apply Compliance`
   - Set voltage and click `Apply Voltage`
   - Click `Measure Current` (or `Start Live`)
3. `Sweep` tab:
   - Choose subsection and run (details below)

## Sweep Subsections
### 1) Standard Sweep
- Fields: `Start`, `Stop`, `Step`, `Delay`, `Mode`, `Cycle Peak`, `Cycles`
- Modes:
  - `One-way`
  - `Simple Cycle (0->+V->0->-V->0)`
- Click `Run Sweep (F5)`

### 2) Custom Sequence
- Build segments with `Seg Start V` and `Seg End V`
- Click `Add Segment (Append)` for each segment
- Use `Update Selected` / `Delete Selected` / `Reset Segments` as needed
- Set `Sequence Cycles`
- Click `Run Custom Sequence`

Example sequence:
- `0 -> 2`
- `2 -> -3`
- `-3 -> 0`
- Then repeat by setting sequence cycles

### 3) WRER
- Fields:
  - `Write V`, `Write Time`
  - `Read V`, `Read Time`
  - `Erase V`, `Erase Time`
  - `Cycles`
- Uses sequence: `Write -> Read -> Erase -> Read`
- Uses common `Delay (s)` as sampling interval
- `Step` is intentionally unused in WRER
- Click `Run WRER`

## Execution Mode
In Sweep `Common Settings`:
- `Host (UI timing)`:
  - Safer/general mode
  - Delay minimum enforced in UI for stable operation
- `Fast TSP (instrument timing)`:
  - For Keithley 2600 TSP mode
  - Faster internal execution, data fetched in bulk

## Data + Plot
- Before each sweep run, app asks save file path.
- If file exists:
  - `Yes` = append
  - `No` = overwrite
  - `Cancel` = abort
- `Data` section:
  - `Save CSV`, `Load CSV`, `Choose Save Folder`, `Open Save Folder`
- Plot behavior:
  - Standard/Custom: I-V plot
  - WRER: two-panel time plot

## Persisted Settings
App stores last-used values in `keithley_ui_settings.json`, including:
- Most entry fields
- Main tab + sweep sub-tab
- Axis selections
- Preferred save folder
- Window geometry
- Custom segments list

## Safety Notes
- Always set compliance before applying high voltage.
- UI enforces max software limits and prompts for high-voltage confirmation.
- On stop/close, output is forced to zero when possible.
- Backend logs: `keithley_backend.log`
