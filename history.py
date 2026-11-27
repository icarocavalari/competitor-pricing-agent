import sqlite3

# Every run goes into one table in this file, with the date of the run as a column
DATABASE = "results/prices.db"

# The columns of the Excel file and of the database table, in this order
COLUMNS = ["competitor", "transparency", "pricing_model", "plan", "seats_included", "credits_per_month",
           "credits_per", "amount", "currency", "period_shown", "billing", "kind", "per_month", "check", "note"]


def connect():
    connection = sqlite3.connect(DATABASE)
    # Creates the table the first time. "check" is a reserved word in SQL, so it goes in quotes.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            run_date TEXT,
            competitor TEXT,
            transparency TEXT,
            pricing_model TEXT,
            plan TEXT,
            seats_included INTEGER,
            credits_per_month INTEGER,
            credits_per TEXT,
            amount REAL,
            currency TEXT,
            period_shown TEXT,
            billing TEXT,
            kind TEXT,
            per_month REAL,
            "check" TEXT,
            note TEXT
        )
    """)
    return connection


def save_run(run_date, rows):
    connection = connect()

    # If a competitor was already saved on this date (the script ran twice), its old rows are replaced
    competitors = set()
    for row in rows:
        competitors.add(row["competitor"])
    for competitor in competitors:
        connection.execute("DELETE FROM prices WHERE run_date = ? AND competitor = ?", (run_date, competitor))

    # One ? for run_date and one for each column
    placeholders = ", ".join(["?"] * (len(COLUMNS) + 1))
    for row in rows:
        values = [run_date]
        for column in COLUMNS:
            values.append(row.get(column))  # None (empty) when the row has nothing for that column
        connection.execute(f"INSERT INTO prices VALUES ({placeholders})", values)

    connection.commit()
    connection.close()


def previous_run_date(run_date):
    connection = connect()
    result = connection.execute("SELECT MAX(run_date) FROM prices WHERE run_date < ?", (run_date,)).fetchone()
    connection.close()
    return result[0]  # None when there's no earlier run


def load_run(run_date):
    connection = connect()
    connection.row_factory = sqlite3.Row  # each row can be read by column name, like a dict
    rows = []
    for row in connection.execute("SELECT * FROM prices WHERE run_date = ?", (run_date,)):
        rows.append(dict(row))
    connection.close()
    return rows


def rows_of(rows, competitor):
    selected = []
    for row in rows:
        if row["competitor"] == competitor:
            selected.append(row)
    return selected


# Only plans and numbers are compared, never texts, because the AI words texts differently on every run

def prices_by_plan(rows):
    # (competitor, plan, billing) -> the monthly prices. A plan without a price has [None].
    prices = {}
    for row in rows:
        key = (row["competitor"], row.get("plan"), row.get("billing"))
        if key not in prices:
            prices[key] = []
        prices[key].append(row.get("per_month"))
    return prices


def details_by_plan(rows):
    # (competitor, plan) -> (seats included, credits per month, credits per seat or per plan)
    details = {}
    for row in rows:
        key = (row["competitor"], row.get("plan"))
        details[key] = (row.get("seats_included"), row.get("credits_per_month"), row.get("credits_per"))
    return details


def has_numbers(details):
    for seats, credits, per in details.values():
        if seats is not None or credits is not None:
            return True
    return False


def show_price(price):
    if price is None:
        return "no price"
    return f"{price:.2f}"


def show_seats(seats):
    if seats is None:
        return "not stated"
    return str(seats)


def show_credits(credits, per):
    if credits is None:
        return "no number"
    return f"{credits} per month, per {per}"


def find_changes(old_rows, new_rows):
    # Each change is (change, competitor, plan, billing, before, after)
    changes = []

    # 1. Prices
    old_prices = prices_by_plan(old_rows)
    new_prices = prices_by_plan(new_rows)
    for key in sorted(set(old_prices) | set(new_prices), key=str):
        competitor, plan, billing = key
        before = old_prices.get(key, [])
        after = new_prices.get(key, [])

        removed = []
        for price in before:
            if price not in after:
                removed.append(price)
        added = []
        for price in after:
            if price not in before:
                added.append(price)

        # One price out and one in for the same plan and billing is a new price.
        # With more (like Apollo once showing two prices for one plan), it's not clear which became which.
        if len(removed) == 1 and len(added) == 1:
            changes.append(("price changed", competitor, plan, billing, show_price(removed[0]), show_price(added[0])))
        else:
            for price in removed:
                changes.append(("removed", competitor, plan, billing, show_price(price), "-"))
            for price in added:
                changes.append(("added", competitor, plan, billing, "-", show_price(price)))

    # 2. Seats and credits, for plans in both runs.
    # Runs saved before seats and credits were numbers have none, so there's nothing to compare yet.
    old_details = details_by_plan(old_rows)
    new_details = details_by_plan(new_rows)
    if has_numbers(old_details):
        for key in sorted(new_details, key=str):
            if key not in old_details:
                continue
            competitor, plan = key
            old_seats, old_credits, old_per = old_details[key]
            new_seats, new_credits, new_per = new_details[key]

            if old_seats != new_seats:
                changes.append(("seats changed", competitor, plan, None, show_seats(old_seats), show_seats(new_seats)))
            if old_credits != new_credits or old_per != new_per:
                changes.append(("credits changed", competitor, plan, None,
                                show_credits(old_credits, old_per), show_credits(new_credits, new_per)))

    return changes


def print_changes(old_date, changes):
    if len(changes) == 0:
        print(f"No changes since {old_date}")
        return

    print(f"Changes since {old_date} (prices per month):")
    for change in changes:
        kind, competitor, plan, billing, before, after = change
        where = f"{competitor}, {plan}"
        if billing is not None:
            where += f", {billing}"
        print(f"  {kind}: {where}, {before} -> {after}")


def add_changes_sheet(workbook, old_date, changes):
    sheet = workbook.create_sheet("changes")
    sheet.append(["change", "competitor", "plan", "billing", "before", "after"])
    if old_date is None:
        sheet.append(["no previous run to compare with"])
    elif len(changes) == 0:
        sheet.append([f"no changes since {old_date}"])
    else:
        for change in changes:
            sheet.append(list(change))
    return sheet
