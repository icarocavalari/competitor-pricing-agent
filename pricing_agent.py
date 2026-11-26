import os
import re
import sys
from datetime import date
from typing import Literal, Optional

from anthropic import Anthropic
from dotenv import load_dotenv
from openpyxl import Workbook
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, ValidationError

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
    seats_included: Optional[str]
    credits_included: Optional[str]


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
        max_tokens=4096,
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

    results_folder = f"results/{date.today()}"
    os.makedirs(results_folder, exist_ok=True)
    os.makedirs("debug", exist_ok=True)

    total_cost = 0

    # Every price becomes one row of a table, saved as prices.xlsx at the end for analysis in Excel
    rows = []

    for key in keys:
        name, url = COMPETITORS[key]
        print(f"\n=== {name} ===")

        # 1. Get the page text and keep a copy for checking by eye
        page_text = get_page_text(url)
        with open(f"debug/{key}_page.txt", "w", encoding="utf-8") as file:
            file.write(page_text)

        # 2. Claude extracts the pricing. If the answer comes back broken (cut-off JSON), skip to the next competitor.
        # Any other error, like a wrong setting or no internet, stops the script.
        try:
            pricing, usage = extract(client, name, page_text)
        except ValidationError as error:
            print(f"Extraction failed, skipping {name}: {str(error)[:200]}")
            continue

        # Haiku 5.5 prices: US$0.10 per million input tokens, US$0.50 per million output tokens
        total_cost += usage.input_tokens * 0.10 / 1_000_000
        total_cost += usage.output_tokens * 0.50 / 1_000_000

        # 3. Print the results, checking every price against the page
        print(f"Transparency: {pricing.transparency}")
        print(f"Pricing model: {pricing.pricing_model}")
        print(f"Annual discount: {pricing.annual_discount_text}")

        # A competitor with no plans (like a blocked page) still gets one row, so it shows up in the table
        competitor_row = {
            "competitor": name,
            "transparency": pricing.transparency,
            "pricing_model": pricing.pricing_model,
        }
        if len(pricing.plans) == 0:
            rows.append(competitor_row)

        page = clean(page_text)
        for plan in pricing.plans:
            print(f"  {plan.name}")

            # A plan with no price (like Lusha Scale) also gets one row
            plan_row = dict(competitor_row)
            plan_row["plan"] = plan.name
            plan_row["seats"] = plan.seats_included
            plan_row["credits"] = plan.credits_included
            if len(plan.prices) == 0:
                rows.append(plan_row)

            if plan.seats_included:
                print(f"    Seats: {plan.seats_included}")
            if plan.credits_included:
                print(f"    Credits: {plan.credits_included}")

            if len(plan.prices) == 0:
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
                price_row["check"] = result.split(",")[0]  # only "ok", "REVIEW" or "FAIL"
                rows.append(price_row)

        print(f"Notes: {pricing.notes}")

        # 4. Save the results with today's date
        with open(f"{results_folder}/{key}.json", "w", encoding="utf-8") as file:
            file.write(pricing.model_dump_json(indent=2))

    # 5. Save every price as one table in an Excel file. Numbers stay real numbers, ready for formulas,
    # and it opens correctly in any Excel language (a CSV breaks in Excel set to Portuguese).
    columns = ["competitor", "transparency", "pricing_model", "plan", "seats", "credits",
               "amount", "currency", "period_shown", "billing", "kind", "per_month", "check"]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "prices"
    sheet.append(columns)
    for row in rows:
        values = []
        for column in columns:
            values.append(row.get(column))  # an empty cell when the row has no value for that column
        sheet.append(values)
    workbook.save(f"{results_folder}/prices.xlsx")

    print(f"\nTotal cost: about US${total_cost:.4f}")


if __name__ == "__main__":
    main()
