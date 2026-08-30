# Alpaca AI Trading Agents Hackathon: rules, stipulations, and live Q&A

Compiled 2026-08-28 from three sources: the event page, the lablab.ai Rule
Book / Guidelines, and the full text transcript of the live "General Q&A
Stage" Discord session (9:00-9:38 AM PDT, staffed by Alpaca's Brandon,
Danny, and Grace, plus lablab's Zofia and Steve). The Kick-Off Stream itself
(YouTube, 8:00 AM PDT) has captions disabled, so nothing from the spoken
narration of that stream is captured here beyond what's already written on
the event page.

## 1. Core requirements (event page)

- Build an **autonomous** AI trading agent using Alpaca's Trading API.
- Must use **Alpaca's MCP server or CLI** (not required to use both).
- Strategy must **incorporate options trading** somewhere, but is not limited
  to options only — equities, crypto, futures can be layered in per Q&A
  (see section 3).
- Main track: "Options Alpha Agents" — build a strategy that generates P&L,
  demonstrate opportunity ID, decisioning, position management, and
  performance over the competition.

## 2. Account requirements

- Use any paper account to explore/build.
- **For judging: a brand-new, dedicated Alpaca paper account**, never reused
  from a prior project. Starting balance must be **$100,000**.
- Vivek's dedicated account (per `registration.md`, still current):
  - Nickname: `Refusal Rails`
  - Account ID: `PA3PZGSB3W2E`
  - Balance: $100,000.00, untouched as of registration
  - Do not touch the old paper account `PA3GFFYS3PYL` for this event.

## 3. Live Q&A — the actual stipulations (staff-confirmed answers only)

Organized by topic, staff name in brackets, timestamps PDT.

**Data & market feed**
- Paper trading fills use **live quotes**, not a 15-minute delayed tape.
  Option chains/latest quotes via Market Data API are real-time too. Free
  Basic plan = Indicative options feed (live, not full OPRA); Algo Trader
  Plus = full OPRA. The only 15-min restriction on Basic is pulling the most
  recent 15 minutes of **historical** bars/trades. — Danny (Alpaca), 9:16 AM
- **External/qualitative data sources on top of Alpaca are allowed.** —
  Danny (Alpaca), 9:27 AM

**MCP / CLI / tooling**
- The **official Alpaca MCP server already covers options**: contracts,
  full chains, quotes, Greeks, and `place_option_order` for single- and
  multi-leg orders. You do not need `alpaca-py` just for options — use it or
  the REST API if you want the trading loop in your own code. **MCP option
  orders are market and limit only** (no stop-limit/trailing-stop via MCP).
  — Danny (Alpaca), 9:16 AM
- Using a **public GitHub prebuilt stack for a layer** (e.g. routing) is
  fine — call it out in the write-up/README. — Danny (Alpaca), 9:30 AM
- No explicit statement on what counts as "proof" of MCP integration beyond
  working functionality (a participant asked, 9:31 AM, question was not
  directly answered in the transcript).

**Account / trading window rules**
- **Only a paper account is necessary** for the hackathon (no live account
  needed). — Brandon (Alpaca), 9:16 AM
- **Only performance on the fresh, dedicated competition account counts**
  toward P&L judging. You can develop/test on other paper accounts first,
  then switch to the fresh one. — Brandon (Alpaca), 9:16 AM
- "You can develop and test any way you want, but **for official judging you
  must use a fresh paper account and all trades inside that account must
  fall inside the competition period**." — Brandon (Alpaca), 9:17 AM (edited
  9:19 AM)
- Multiple agents/strategies per team are fine, but **all trading during the
  competition period must happen inside only one paper account.** — Brandon
  (Alpaca), 9:25 AM
- Alpaca API **rate limit is 200 requests/min. No cap on number of trades.**
  — Grace (Alpaca), 9:29 AM
- Competition window is the full **7-day hackathon (28 Aug - 4 Sep)**, not
  just the ~4-5 weekday trading days some participants assumed — lablab
  repeated this twice when pushed back on. — Zofia (lablab), 9:23 AM

**Judging criteria**
- Ending P&L is scored as **ending account equity** (not just realized
  gains, not explicitly risk-adjusted metrics like Sharpe/Sortino — those
  weren't confirmed as judged directly). — Grace (Alpaca), 9:27 AM
- "**PnL is only partial of the judgement criteria. We will evaluate based
  on the creativity of the agentic solution/workflow as well.**" — Grace
  (Alpaca), 9:24 AM
- "**It will be all equally weighted**" — re: whether P&L, creativity, and
  technical implementation are weighted differently. — Grace (Alpaca), 9:29 AM
- Backtests / historical logs / simulated market shocks **can be submitted
  alongside** the live paper account as supporting evidence of the risk
  agent's thinking — but **paper equity is what's actually scored.**
  `scipy` / `statsmodels` are explicitly welcomed for risk analytics; name
  the packages in the write-up/README. — Danny (Alpaca), 9:37 AM (message
  edited/reposted 9:51 AM)
- Full written judging criteria (Presentation, Business Value, Application
  of Technology, Originality) are on the event page — lablab repeatedly
  pointed a persistently-confused participant there rather than restating
  it live. — Zofia (lablab), 9:31 AM

**Not directly answered in the Q&A** (asked, no staff reply captured):
- Whether 0 DTE (zero days to expiry) options are allowed.
- What exactly "MIT-compliant" means for the repo license.
- Whether GitHub commit history must start at the official kickoff vs. can
  predate it from registration.
- Whether trading value/activity *after* the submission deadline still
  counts.
- Minimum trade count/activity threshold for P&L eligibility.
- Whether Stop-Limit / Trailing-Stop order types are supported on paper
  accounts via non-MCP paths (only the MCP-specific market/limit-only
  restriction was confirmed).

## 4. Written Rule Book stipulations (lablab.ai/hackathon-rules)

- **Submission components**: project title, short/long description, tech &
  category tags, cover image (PNG/JPG, 16:9), video presentation (MP4) and
  slide presentation (PDF), public GitHub repo, demo app URL (Streamlit,
  Replit, or Vercel-hosted), and — specific to this event — the **Alpaca
  paper trading account ID** (required for judging to trace P&L).
- **Manual submission window**: available for **6 hours post-hackathon**
  only for participants with valid reasons and **prior approval** from
  organizers/mentors.
- **Ethical conduct**: plagiarism, gaming the voting system, unauthorized
  automation, or fraudulent behavior is grounds for immediate
  disqualification.
- **Mentor/organizer participation**: allowed to build, but **not eligible
  for prizes**; if they participate they cannot also judge.
- Judges must keep submissions confidential, recuse on conflicts of
  interest, and not copy/retain/share entry materials.

## 5. Team / process (already actioned)

- Team "Refusal Rails" created (solo, Closed to new members) — done this
  session, confirmed via My Teams page.
- Submission checklist per `registration.md` still open: public repo with
  `.env.example`, hosted prototype, video presentation, pitch deck/slide
  deck, and (per Rule Book) the demo must be on Streamlit/Replit/Vercel.

## 6. Social engagement (optional, extra prize track)

- Up to 5 X/LinkedIn post links can be submitted, tagging both `@lablabai`/
  lablab.ai and `@AlpacaHQ`/Alpaca. Two winning teams get $500 + 1-month Algo
  Trader Plus per member for this track specifically.

## Sources

- https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon (event page)
- https://lablab.ai/hackathon-rules (Rule Book)
- https://lablab.ai/ai-articles/hackathon-guidelines (Guidelines)
- Discord "General Q&A Stage" text chat transcript, 28 Aug 2026 9:11-9:38 AM
  PDT (raw capture saved to
  `/Users/Vivek/.aside/u/0/sessions/2026-08-28_WtjShgZg9W75m4ly/tmp/qa_stage_raw.txt`
  for reference)
