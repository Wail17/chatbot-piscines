# Company knowledge

Compact, hand-curated context about the two companies behind this chatbot.
Loaded by `app/rag.py` and prepended to the cached system prompt so Claude
treats it as core knowledge.

A more exhaustive auto-scrape of both websites is kept in
`app/data/company_knowledge_full_scrape.md` for manual review, but is NOT
loaded into the prompt (it is too noisy and was confusing Claude).

---

# Beniferro

**Website:** https://beniferro.eu
**Type:** Belgian manufacturer of smart pool products (plug & play DIY-friendly hardware).
**Sister webshop:** https://www.zwembad.eu (this is where customers actually order most equipment).

## What Beniferro makes

- **Wifipool controllers** — smart pool controllers in three generations:
  - Wifipool **Gen 1** — legacy generation
  - Wifipool **Gen 2** — current standard generation, with Ethernet support
  - Wifipool **Gen 3** — newest generation
- **Wifipool Twin** — pre-mounted plug & play water treatment installation based on **liquid chlorine + pH** dosing (acid + chlorine peristaltic pumps).
- **Wifipool Duo** — pre-mounted plug & play water treatment installation based on **salt electrolysis + pH** dosing (acid + salt electrolyser).
- **Pool Twin / Pool Duo** are the same product family — referred to as "Pool Twin" and "Pool Duo" in shorter form in some FAQ entries.
- **pH and ORP (Redox / RX) measurement & dosing systems** — peristaltic dosing pumps, pH and Redox probes.
- **Salt electrolysis (zoutelektrolyse) units** — for chlorine generation from salt.
- **Frequency regulators (frequentieregelaars)** for filtration pumps — variable-speed pump control.
- **Flow meters and level switches** — Wi-Fi-connected.
- **Temperature measurement** — Wi-Fi probes.
- **EPDM solar heating panels** — and associated automated control valves.
- **Smart switches** — based on Shelly hardware for accessory control (lights, UV lamps, heat pumps).

## The Wifipool ecosystem

The whole product line is managed from the **Wifipool app**, available for iOS and Android. The app handles pairing, automation rules, manual control, calibration of sensors, and per-pool settings.

A typical Wifipool installation combines a controller (Gen 1/2/3) with a selection of measurement modules (pH, Redox, temperature, flow) and dosing/relay modules. The owner can mix and match.

## Who Beniferro is for

DIY pool owners and pool installers who want full remote control and automation of their pool without manual chemical balancing. Wifipool products are designed to be **pre-assembled and easy to install** (plug & play).

## Languages

Beniferro products and documentation are sold across the Benelux + DE region — the chatbot serves customers in Dutch (NL), French (FR), English (EN) and German (DE).

---

# Zwembad.eu

**Website:** https://www.zwembad.eu
**Type:** Webshop selling complete pool equipment and Beniferro/Wifipool products to end customers.

## What Zwembad.eu sells

- **Complete pool kits** — frame pools, wooden pools, metal pools, oval/rectangular/round, inground & above-ground.
- **Inflatable and frame pools** (kinderzwembaden, opzetzwembaden).
- **Pool accessories** — pump parts, filters, hoses, replacement parts, special pool concepts.
- **Beniferro / Wifipool products** — controllers, dosing equipment, sensors, salt electrolysis units, solar heating.

## Practical info

- Webshop ships to Belgium and surrounding countries.
- Catalogue is the place to point customers to when they ask **where to buy** a Beniferro or Wifipool product.
- All Wifipool / Beniferro technical questions belong here (in this chatbot); product purchase questions belong on the Zwembad.eu webshop.

---

# Usage rules for Claude

- If a user asks **"What does Beniferro make?"** → list the Wifipool ecosystem above (controllers, dosing, sensors, electrolysis, solar, smart switches).
- If a user asks **"Where can I buy product X?"** → point them to https://www.zwembad.eu.
- If a user asks for **the price** of a specific product → say you don't have live pricing; direct them to the Zwembad.eu product page.
- If a user asks for **the difference between Wifipool Twin and Wifipool Duo** → Twin = **liquid chlorine + pH** dosing; Duo = **salt electrolysis + pH** dosing. Both are plug & play pre-mounted installations.
- If a user asks something about pools that is **not** covered here AND **not** in the FAQ knowledge base — say so honestly. Do not substitute with an unrelated FAQ topic.
