import os
import re
import sys
from datetime import date
from typing import Literal, Optional

from anthropic import Anthropic
from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, ValidationError

from discounts import add_discounts_sheet, find_discounts, print_discounts
from history import (COLUMNS, add_changes_sheet, find_changes, load_run, previous_run_date, print_changes,
                     rows_of, save_run)

MODEL = "claude-haiku-5-5"

COMPETITORS = {
    "salesnav": ("LinkedIn Sales Navigator", "https://business.linkedin.com/sell/sales-navigator/compare-plans"),
    "apollo": ("Apollo.io", "https://www.apollo.io/pricing"),
    "lusha": ("Lusha", "https://www.lusha.com/pricing/"),
    "zoominfo": ("ZoomInfo", "https://www.zoominfo.com/pricing"),
    "cognism": ("Cognism", "https://www.cognism.com/pricing"),
}

# The exact texts a "monthly" button can have. A sentence like "Billed monthly" doesn't count.
MONTHLY_LABELS = ["monthly", "pay monthly", "monthly plan", "monthly billing"]


# Reads every piece of text on the page, one per line, including hidden text like collapsed FAQ answers
# (page.inner_text only returns what's visible on screen)
ALL_TEXT = """() => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const lines = [];
    while (walker.nextNode()) {
        const tag = walker.currentNode.parentElement.tagName;
        const text = walker.currentNode.textContent.trim();
        if (text && !["SCRIPT", "STYLE", "NOSCRIPT"].includes(tag)) {
            lines.push(text);
        }
    }
    return lines.join("\\n");
}"""


class Price(BaseModel):
    amount: float
    currency: Optional[str]
    billing: Literal["monthly", "annual", "unknown"]
    period_shown: Literal["month", "year", "unknown"]
    unit: str
    kind: Literal["current", "original", "promotional", "unclear"]
    evidence: str


class Plan(BaseModel):
    name: str
    prices: list[Price]
    seats_included: Optional[int]
    seats_evidence: Optional[str]
    credits: Optional[int]
    credits_period: Literal["month", "year", "unknown"]
    credits_per_seat: bool
    credits_evidence: Optional[str]


class Pricing(BaseModel):
    transparency: Literal["public", "contact_sales", "gated_form", "no_prices_in_text", "blocked"]
    annual_discount_text: Optional[str]
    plans: list[Plan]
    # After plans on purpose. The AI writes top to bottom, so it picks the model after writing out seats and credits.
    pricing_model: Literal["per_seat", "credits", "seat_plus_credits", "flat", "unknown"]
    notes: str


# The instructions for Claude live in rules.md, so this file stays focused on the Python code
with open("rules.md", encoding="utf-8") as file:
    RULES = file.read()


def get_page_text(url):
    with sync_playwright() as p:
        # Your real Chrome with a visible window. Sites block it much less than the hidden test browser.
        browser = p.chromium.launch(channel="chrome", headless=False)
        page = browser.new_page(locale="en-US")
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

        text = "VIEW 1, page as it loads\n"
        text += page.evaluate(ALL_TEXT)

        # Most pages open on annual prices. If there's a monthly button, click it and read the page again.
        monthly_button = None
        for element in page.get_by_text("monthly").all():
            element_text = element.inner_text().strip().lower()
            if element_text in MONTHLY_LABELS and element.is_visible():
                monthly_button = element
                break

        if monthly_button is not None:
            try:
                monthly_button.click()
                page.wait_for_timeout(2000)
                text += "\n\nVIEW 2, after clicking monthly\n"
                text += page.evaluate(ALL_TEXT)
            except Exception as error:
                print(f"  Could not click the monthly button: {error}")

        browser.close()

    return text


def extract(client, name, page_text):
    response = client.messages.parse(
        model=MODEL,
        # Room for the model's hidden reasoning plus the answer. Only the tokens actually used are paid for.
        max_tokens=16000,
        system=RULES,
        messages=[{"role": "user", "content": f"Company: {name}\n\n{page_text}"}],
        output_format=Pricing,
    )
    return response.parsed_output, response.usage


def clean(text):
    text = text.lower()
    text = " ".join(text.split())    # all spaces and line breaks become one space
    text = text.replace("$ ", "$")   # "$ 37" -> "$37"
    text = text.replace(" .", ".")   # "37 .45" -> "37.45"
    text = text.replace(",", "")     # "month, billed" -> "month billed", "1,079.88" -> "1079.88"
    return text


def check(price, page, plan):
    evidence = clean(price.evidence)

    if evidence == "":
        return "FAIL, no evidence"

    if evidence not in page:
        return "FAIL, evidence not found in the page"

    # A number like 37, 1,079.88 or 0. A digit, then digits or commas, then optionally a dot and decimals.
    number_pattern = r"\d[\d,]*(?:\.\d+)?"

    # Numbers right after a "$" are prices. If there is no "$", use every number in the evidence.
    found = re.findall(r"\$(" + number_pattern + ")", evidence)
    if len(found) == 0:
        found = re.findall(number_pattern, evidence)

    numbers = []
    for number in found:
        numbers.append(float(number.replace(",", "")))

    is_free_plan = price.amount == 0 and "free" in evidence
    if price.amount not in numbers and not is_free_plan:
        return "FAIL, price not in its evidence"

    # Two prices for the same plan, shown per the same period, with different amounts, and the billing doesn't
    # tell them apart (same billing, or one is unknown). The page contradicts itself, so a human checks.
    # A discount ("original" or "promotional" next to "current") is not a contradiction.
    for other in plan.prices:
        same_period = other.period_shown == price.period_shown
        different_amount = other.amount != price.amount

        same_billing = other.billing == price.billing
        unknown_billing = other.billing == "unknown" or price.billing == "unknown"

        # One of the two is the price before a discount (crossed out) or a temporary promo price.
        # Then the pair is a discount being shown, not a contradiction.
        kinds = [other.kind, price.kind]
        pair_is_discount = "original" in kinds or "promotional" in kinds

        if same_period and different_amount and (same_billing or unknown_billing) and not pair_is_discount:
            return "REVIEW, two different prices for the same plan and billing"

    if price.kind == "unclear":
        return "REVIEW, not clear which price is current"

    return "ok"


def per_month(price):
    if price.period_shown == "year":
        return round(price.amount / 12, 2)
    if price.period_shown == "month":
        return price.amount
    return None


# Words that mean a price or credits are for one person
PER_SEAT_WORDS = ["per seat", "per user", "per license", "/license"]


def says_per_seat(text):
    for words in PER_SEAT_WORDS:
        if words in text:
            return True
    return False


def found_on_page(text, page):
    # The AI's copy of the page text, checked against the page. Empty or missing never counts as found.
    if text is None:
        return False
    text = clean(text)
    return text != "" and text in page


def check_plan(plan, page):
    # Seats. The text is on the page and the number is in it.
    # A price per seat ("Per seat per month") counts as 1 seat, even without the number 1.
    if plan.seats_included is not None:
        if not found_on_page(plan.seats_evidence, page):
            return "REVIEW, seats text not found in the page"
        evidence = clean(plan.seats_evidence)
        per_seat_price = plan.seats_included == 1 and says_per_seat(evidence)
        if str(plan.seats_included) not in evidence.split() and not per_seat_price:
            return "REVIEW, seats number not in its text"

    # Credits. The text is on the page, and the number and the period (month or year) are in it.
    if plan.credits is not None:
        if not found_on_page(plan.credits_evidence, page):
            return "REVIEW, credits text not found in the page"
        evidence = clean(plan.credits_evidence)
        if str(plan.credits) not in evidence.split():
            return "REVIEW, credits number not in its text"
        if plan.credits_period not in evidence:
            return "REVIEW, credits period not in its text"

        # Per seat or per plan, checked both ways. Python reads the page right after the credits itself,
        # so it also catches a "per seat" that the AI left out of its copy.
        position = page.find(evidence)
        nearby = page[position:position + len(evidence) + 30]
        if plan.credits_per_seat != says_per_seat(nearby):
            return "REVIEW, per seat or per plan doesn't match the page"

    return "ok"


def credits_per_month(plan):
    if plan.credits is None:
        return None
    if plan.credits_period == "year":
        return round(plan.credits / 12)
    if plan.credits_period == "month":
        return plan.credits
    return None


def credits_per(plan):
    if plan.credits is None:
        return None
    if plan.credits_per_seat:
        return "seat"
    return "plan"


def worst(first, second):
    # Keeps the more serious of two results. FAIL first, then REVIEW, then ok.
    for level in ["FAIL", "REVIEW"]:
        if first.startswith(level):
            return first
        if second.startswith(level):
            return second
    return "ok"


def add_check(row, result):
    # "REVIEW, not clear which price is current" -> check "REVIEW", note "not clear which price is current"
    status, _, note = result.partition(", ")
    row["check"] = status
    if note != "":
        row["note"] = note


def format_sheet(sheet):
    # 1. Header row: bold white text on a blue background
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(fill_type="solid", start_color="2F5DA8")

    # 2. Keep the header visible while scrolling
    sheet.freeze_panes = "A2"

    # 3. Make each column as wide as its longest value (plus some space for the bold header), up to 40 characters
    for column in sheet.columns:
        longest = 0
        for cell in column:
            if cell.value is not None:
                longest = max(longest, len(str(cell.value)))
        sheet.column_dimensions[column[0].column_letter].width = min(longest + 4, 40)

    # 4. Show prices with two decimals (49.00 instead of 49)
    for cell in sheet[1]:
        if cell.value in ("amount", "per_month"):
            for price_cell in sheet[cell.column_letter][1:]:
                price_cell.number_format = "0.00"

    # 5. Color the check column, so anything a person has to look at stands out
    for cell in sheet[1]:
        if cell.value == "check":
            for check_cell in sheet[cell.column_letter][1:]:
                if check_cell.value == "ok":
                    check_cell.font = Font(color="1E7B34")
                elif check_cell.value == "REVIEW":
                    check_cell.font = Font(bold=True, color="B45F06")
                elif check_cell.value == "FAIL":
                    check_cell.font = Font(bold=True, color="C00000")


def cost(usage):
    # Haiku 5.5 prices: US$0.10 per million input tokens, US$0.50 per million output tokens
    return usage.input_tokens * 0.10 / 1_000_000 + usage.output_tokens * 0.50 / 1_000_000


# Prints Claude's answer, checks every price, seat and credit against the page, and turns it into table rows
def build_rows(name, pricing, page):
    rows = []
    print(f"Transparency: {pricing.transparency}")
    print(f"Pricing model: {pricing.pricing_model}")
    print(f"Annual discount: {pricing.annual_discount_text}")

    # A competitor with no plans (like a blocked page) still gets one row, so it shows up in the table
    competitor_row = {
        "competitor": name,
        "transparency": pricing.transparency,
        "pricing_model": pricing.pricing_model,
    }

    # A blocked page can't be read, so a person has to check it by hand
    if pricing.transparency == "blocked":
        add_check(competitor_row, "REVIEW, page blocked, check it by hand")
        print("Blocked, a person has to check this page by hand -> REVIEW")

    if len(pricing.plans) == 0:
        rows.append(competitor_row)

    for plan in pricing.plans:
        print(f"  {plan.name}")

        plan_result = check_plan(plan, page)
        if plan.seats_included is not None:
            print(f"    Seats: {plan.seats_included} ({plan.seats_evidence})")
        if plan.credits is not None:
            print(f"    Credits: {plan.credits} per {plan.credits_period}, per {credits_per(plan)} "
                  f"({plan.credits_evidence})")
        if plan.seats_included is not None or plan.credits is not None:
            print(f"    Seats and credits -> {plan_result}")

        # A plan with no price (like Lusha Scale) also gets one row
        plan_row = dict(competitor_row)
        plan_row["plan"] = plan.name
        plan_row["seats_included"] = plan.seats_included
        plan_row["credits_per_month"] = credits_per_month(plan)
        plan_row["credits_per"] = credits_per(plan)
        if len(plan.prices) == 0:
            add_check(plan_row, plan_result)
            rows.append(plan_row)
            print("    No price")

        for price in plan.prices:
            currency = price.currency
            if currency is None:
                currency = ""

            monthly = per_month(price)
            if monthly is None:
                monthly = "?"

            result = check(price, page, plan)
            print(f"    {price.amount} {currency} per {price.period_shown} "
                  f"({price.billing} billing, {price.kind}) = {monthly} per month -> {result}")

            price_row = dict(plan_row)
            price_row["amount"] = price.amount
            price_row["currency"] = price.currency
            price_row["period_shown"] = price.period_shown
            price_row["billing"] = price.billing
            price_row["kind"] = price.kind
            price_row["per_month"] = per_month(price)
            add_check(price_row, worst(result, plan_result))
            rows.append(price_row)

    print(f"Notes: {pricing.notes}")
    return rows


# answers is a list of (pricing, rows). Two answers agree when comparing them finds no changes.
def answer_that_repeats(answers):
    for i in range(len(answers)):
        for j in range(i + 1, len(answers)):
            if len(find_changes(answers[i][1], answers[j][1])) == 0:
                print(f"\nAnswers {i + 1} and {j + 1} agree, keeping that answer")
                return answers[i]

    # No two answers agree, so a person has to check this competitor
    print("\nThe answers didn't repeat -> REVIEW")
    pricing, rows = answers[0]
    for row in rows:
        if row.get("check") != "FAIL":
            add_check(row, "REVIEW, the AI gave a different answer each time")
    return pricing, rows


def main():
    if len(sys.argv) > 1:
        keys = sys.argv[1:]
    else:
        keys = list(COMPETITORS)

    for key in keys:
        if key not in COMPETITORS:
            sys.exit(f"Unknown competitor '{key}'. Options: {', '.join(COMPETITORS)}")

    load_dotenv()
    client = Anthropic()

    today = str(date.today())
    results_folder = f"results/{today}"
    os.makedirs(results_folder, exist_ok=True)
    os.makedirs("debug", exist_ok=True)

    # The previous run, from the database, to compare with
    previous = previous_run_date(today)
    previous_rows = []
    if previous is not None:
        previous_rows = load_run(previous)

    total_cost = 0
    rows = []      # every price of this run becomes one row of a table
    changes = []   # everything that changed since the previous run

    for key in keys:
        name, url = COMPETITORS[key]
        print(f"\n=== {name} ===")

        # 1. Get the page text and keep a copy for checking by eye
        page_text = get_page_text(url)
        with open(f"debug/{key}_page.txt", "w", encoding="utf-8") as file:
            file.write(page_text)
        page = clean(page_text)

        # 2. Claude extracts the pricing, and Python checks it and turns it into rows.
        # If the answer comes back broken (cut-off JSON), skip to the next competitor.
        # Any other error, like a wrong setting or no internet, stops the script.
        try:
            pricing, usage = extract(client, name, page_text)
        except ValidationError as error:
            print(f"Extraction failed, skipping {name}: {str(error)[:200]}")
            continue
        total_cost += cost(usage)
        competitor_rows = build_rows(name, pricing, page)

        # 3. If something changed since the previous run, Claude reads the same page two more times and the
        # answer that repeats is kept, so a random AI mistake isn't taken for a real price change
        old_rows = rows_of(previous_rows, name)
        if len(old_rows) > 0 and len(find_changes(old_rows, competitor_rows)) > 0:
            answers = [(pricing, competitor_rows)]
            for attempt in [1, 2]:
                print(f"\n--- Something changed since {previous}. Reading again to confirm ({attempt} of 2) ---")
                try:
                    pricing, usage = extract(client, name, page_text)
                except ValidationError:
                    print("Extraction failed")
                    continue
                total_cost += cost(usage)
                answers.append((pricing, build_rows(name, pricing, page)))
            pricing, competitor_rows = answer_that_repeats(answers)

        if previous is not None:
            changes.extend(find_changes(old_rows, competitor_rows))
        rows.extend(competitor_rows)

        # 4. Save Claude's answer with today's date
        with open(f"{results_folder}/{key}.json", "w", encoding="utf-8") as file:
            file.write(pricing.model_dump_json(indent=2))

    # 5. Save every price as one table in an Excel file. Numbers stay real numbers, ready for formulas,
    # and it opens correctly in any Excel language (a CSV breaks in Excel set to Portuguese).
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "prices"
    sheet.append(COLUMNS)
    for row in rows:
        values = []
        for column in COLUMNS:
            values.append(row.get(column))  # an empty cell when the row has no value for that column
        sheet.append(values)
    format_sheet(sheet)

    # 6. Add this run to the database. What changed and the annual discounts go in two more sheets.
    save_run(today, rows)

    print()
    if previous is None:
        print("No previous run to compare with")
    else:
        print_changes(previous, changes)
    format_sheet(add_changes_sheet(workbook, previous, changes))

    print()
    discounts = find_discounts(rows)
    print_discounts(discounts)
    format_sheet(add_discounts_sheet(workbook, discounts))

    workbook.save(f"{results_folder}/prices.xlsx")

    print(f"\nTotal cost: about US${total_cost:.4f}")


if __name__ == "__main__":
    main()
