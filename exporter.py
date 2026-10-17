"""
Turns a recording into an Excel (.xlsx) file.

Sheet "Readings": one row per snapshot, one column per sensor measurement, with the
unit in each header. A blank cell means that sensor gave no fresh reading then.
Sheet "Summary": details of the recording, and per-column count / min / max / average
as Excel formulas over the Readings sheet (so they update if you edit the data).

The workbook is written in streaming mode, so a long recording doesn't have to fit
in memory all at once.
"""
import io
import time
from datetime import datetime

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.indexed_list import IndexedList

from recorder import DEFAULT_STALE_AFTER

MAX_SHEET_ROWS = 1_048_576            # Excel's limit per sheet
MAX_DATA_ROWS = MAX_SHEET_ROWS - 1    # minus the header row
FONT = "Arial"
TEAL = "1F6F78"                       # the dashboard's colour
DATETIME_FORMAT = "yyyy-mm-dd hh:mm:ss"

UNIT_NAMES = {"Cel": "°C", "bool": "0 / 1"}


def unit_label(unit):
    return UNIT_NAMES.get(unit, unit or "")


def column_label(col, show_sensor):
    """Header text for one series, e.g. 'DHT22 temperature (°C)'."""
    base = col["model"]
    if show_sensor:
        base += f" [{col['sensor_id']}]"
    label = f"{base} {col['measurement'].replace('_', ' ')}"
    unit = unit_label(col["unit"])
    return f"{label} ({unit})" if unit else label


def export_filename(session):
    started = datetime.fromtimestamp(session["started"]).strftime("%Y-%m-%d_%H%M")
    return f"mulsecube_{started}.xlsx"


def _use_arial(wb):
    """Make Arial the workbook's base font. Best-effort: if openpyxl changes internally, skip it."""
    try:
        arial = Font(name=FONT, size=10, family=2)
        wb._fonts = IndexedList([arial])
        wb._named_styles["Normal"].font = arial
    except Exception:
        pass


def build_workbook(db, session_id, stale_after=DEFAULT_STALE_AFTER):
    """Returns (BytesIO holding the .xlsx, suggested filename). Raises LookupError if no such recording."""
    session = db.get_session(session_id)
    if session is None:
        raise LookupError(f"There is no recording with id {session_id}.")

    columns = db.session_columns(session_id)
    sensors_per_model = {}
    for c in columns:
        sensors_per_model.setdefault(c["model"], set()).add(c["sensor_id"])
    labels = [column_label(c, len(sensors_per_model[c["model"]]) > 1) for c in columns]

    wb = Workbook(write_only=True)
    _use_arial(wb)
    readings = wb.create_sheet("Readings")
    summary = wb.create_sheet("Summary")

    # ------------------------------------------------------------ Readings --
    readings.freeze_panes = "B2"
    readings.column_dimensions["A"].width = 21
    for i, label in enumerate(labels, start=2):
        readings.column_dimensions[get_column_letter(i)].width = min(max(len(label) + 2, 16), 42)

    def header(text):
        cell = WriteOnlyCell(readings, value=text)
        cell.font = Font(name=FONT, size=10, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", start_color=TEAL)
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        return cell

    readings.append([header("Time")] + [header(label) for label in labels])

    index = {(c["sensor_id"], c["model"], c["measurement"]): i + 1 for i, c in enumerate(columns)}
    rows_written = 0
    truncated = False
    current_ts = None
    row = None

    for ts, sensor_id, model, measurement, value in db.iter_samples(session_id):
        if ts != current_ts:
            if row is not None:
                readings.append(row)
                rows_written += 1
            if rows_written >= MAX_DATA_ROWS:
                truncated = True
                row = None
                break
            current_ts = ts
            row = [datetime.fromtimestamp(round(ts))] + [None] * len(columns)
        row[index[(sensor_id, model, measurement)]] = value
    if row is not None:
        readings.append(row)
        rows_written += 1

    last_row = rows_written + 1       # row 1 is the header

    # ------------------------------------------------------------- Summary --
    summary.column_dimensions["A"].width = 40
    summary.column_dimensions["B"].width = 26
    for letter in "CDEFG":
        summary.column_dimensions[letter].width = 13

    def cell(value, bold=False, size=10, number_format=None, fill=None, color=None, wrap=False):
        c = WriteOnlyCell(summary, value=value)
        c.font = Font(name=FONT, size=size, bold=bold, color=color)
        if number_format:
            c.number_format = number_format
        if fill:
            c.fill = PatternFill("solid", start_color=fill)
        if wrap:
            c.alignment = Alignment(wrap_text=True, vertical="top")
        return c

    has_data = rows_written > 0
    time_range = f"Readings!A2:A{last_row}"
    summary.append([cell("MulseCube recording", bold=True, size=14)])
    summary.append([])
    summary.append([cell("Name", bold=True), cell(session["name"])])
    summary.append([cell("Started", bold=True), cell(datetime.fromtimestamp(round(session["started"])),
                                                     number_format=DATETIME_FORMAT)])
    summary.append([cell("Last reading", bold=True),
                    cell(f"=MAX({time_range})", number_format=DATETIME_FORMAT) if has_data else cell("-")])
    summary.append([cell("Duration (hh:mm:ss)", bold=True),
                    cell("=B5-B4", number_format="[h]:mm:ss") if has_data else cell("-")])
    summary.append([cell("Sampling interval (seconds)", bold=True), cell(session["interval_s"])])
    summary.append([cell("Rows of data", bold=True),
                    cell(f"=COUNT({time_range})") if has_data else cell(0)])
    summary.append([cell("Status when exported", bold=True),
                    cell("Still recording" if session["ended"] is None else "Finished")])
    summary.append([cell("Exported", bold=True), cell(datetime.fromtimestamp(round(time.time())),
                                                      number_format=DATETIME_FORMAT)])
    summary.append([])

    if has_data:
        summary.append([cell(h, bold=True, color="FFFFFF", fill=TEAL)
                        for h in ("Series", "Sensor", "Unit", "Samples", "Min", "Max", "Average")])
        for i, col in enumerate(columns):
            letter = get_column_letter(i + 2)
            rng = f"Readings!{letter}2:{letter}{last_row}"
            summary.append([
                cell(labels[i]),
                cell(col["sensor_id"]),
                cell(unit_label(col["unit"])),
                cell(f"=COUNT({rng})"),
                cell(f"=MIN({rng})"),
                cell(f"=MAX({rng})"),
                cell(f'=IFERROR(AVERAGE({rng}),"")', number_format="0.00"),
            ])
        summary.append([])
    else:
        summary.append([cell("No readings were recorded yet.")])
        summary.append([])

    tz = time.tzname[time.localtime().tm_isdst > 0]
    notes = [
        "Notes",
        f"Times come from the Raspberry Pi's clock (time zone {tz}). A Pi with no internet can "
        f"have the wrong time, so check its clock before relying on absolute times.",
        f"A blank cell on the Readings sheet means that sensor gave no fresh reading (none for "
        f"over {stale_after:g} seconds, or it was unplugged) when the snapshot was taken.",
        "Samples, Min, Max and Average are Excel formulas over the Readings sheet, so they update if you edit the data.",
    ]
    if truncated:
        notes.append(f"This recording is longer than one Excel sheet can hold ({MAX_DATA_ROWS:,} rows). "
                     f"Only the first {MAX_DATA_ROWS:,} rows are included - record with a longer interval to fit more time.")
    summary.append([cell(notes[0], bold=True)])
    for line in notes[1:]:
        summary.append([cell(line)])

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio, export_filename(session)
