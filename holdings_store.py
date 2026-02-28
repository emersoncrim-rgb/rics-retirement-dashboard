import csv
import os
import tempfile


def load_holdings(csv_path):
    """
    Loads holdings from the CSV file, preserving fieldnames and raw row structure.
    Returns (rows, fieldnames).
    """
    csv_path = str(csv_path)

    rows = []
    fieldnames = []

    if not os.path.exists(csv_path):
        return rows, fieldnames

    with open(csv_path, mode="r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames:
            fieldnames = list(reader.fieldnames)
        for row in reader:
            rows.append(row)

    return rows, fieldnames


def validate_holdings(rows):
    """
    Basic validation:
    - shares must be numeric and non-negative
    - ticker must not contain spaces
    """
    errors = []

    for i, row in enumerate(rows):
        # Validate shares
        shares_str = str(row.get("shares", "0")).strip()
        if not shares_str:
            shares_str = "0"

        try:
            shares = float(shares_str)
            if shares < 0:
                errors.append(f"Row {i+1}: Shares cannot be negative.")
        except ValueError:
            errors.append(f"Row {i+1}: Invalid shares value '{row.get('shares')}'.")

        # Validate ticker
        ticker = str(row.get("ticker", "")).strip()
        if " " in ticker:
            errors.append(f"Row {i+1}: Ticker '{ticker}' contains spaces.")

    return errors


def save_holdings(csv_path, rows, fieldnames):
    """
    Atomically saves holdings while preserving schema and column order.
    Returns (ok: bool, errors: list[str]).
    """
    csv_path = str(csv_path)

    if not fieldnames and rows:
        fieldnames = list(rows[0].keys())

    try:
        dir_name = os.path.dirname(csv_path) or "."
        fd, temp_path = tempfile.mkstemp(dir=dir_name, text=True)

        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)

        os.replace(temp_path, csv_path)
        return True, []

    except Exception as e:
        if "temp_path" in locals() and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return False, [f"Failed to save holdings: {str(e)}"]
