# Competitor Pricing Agent

Reads the public pricing pages of LinkedIn Sales Navigator and four competitors (Apollo.io, Lusha, ZoomInfo and Cognism) and turns each one into the same structured format, with plans, prices, billing, seats and credits.

Every price is checked against the page before it's trusted. When something doesn't add up, the price is flagged for a person to review instead of being guessed.

## How it works

1. A real Chrome browser opens each pricing page and reads all of its text, including collapsed FAQ answers. If the page has a monthly toggle, it clicks it and reads the page again.
2. Claude (Haiku 5.5) extracts the pricing into a fixed format. It only uses the page text, copies the exact text behind each price as evidence, and never guesses.
3. Python checks every price. The evidence has to be on the page, and the price has to be in the evidence. Two prices that contradict each other for the same plan are flagged.
4. Each price ends up as ok, REVIEW (a person should look at it) or FAIL (don't trust it). Results are saved by date in `results/`.

Pages that hide their prices are a finding too. The tool records whether prices are public, behind "talk to sales", behind a form, or blocked by a security check.

## Measuring consistency

An AI can give different answers to the same page. To measure that, `consistency_test.py` runs the extraction 10 times per competitor on the same frozen copy of each page and compares every run with the correct answers.

| Round | What changed | Correct runs |
|---|---|---|
| baseline | First version | 31 of 50 |
| clear_rules | Clearer rules for currencies and for what counts as a plan | 43 of 50 |
| clear_model | Clearer pricing model rule, picked only after the plans are written out | 47 of 50 |
| review_check | Python check for contradicting prices, prices allowed from the FAQ | 49 of 50 |

Most of the variation came from vague instructions, not randomness. When a rule left room for interpretation, the AI decided differently from run to run. The summary of each round is in `tests/`.

## Cost

A full run over the five competitors costs about US$0.01. A test round of 50 runs costs about US$0.10.

## Next steps

- Run it every morning and compare with the previous day. Any difference goes to a person, since it's either a real price change or a page the tool no longer reads correctly.
- When a result differs, run it twice more and keep the majority, so a rare AI mistake doesn't trigger a false alert.
- A report comparing plans and prices across competitors.

## Running it

Needs Python 3.9+, Google Chrome and an Anthropic API key in a `.env` file as `ANTHROPIC_API_KEY`.

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python pricing_agent.py
```

To run a single competitor, pass its name, for example `python pricing_agent.py apollo`.
