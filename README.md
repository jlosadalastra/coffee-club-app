# Coffee Club App (Streamlit)

## Features
- Group system (admin creates join code, members join)
- Editable postcode prefixes (OX1, OX2, etc.)
- Pull coffee shops from OpenStreetMap
- Map view of shops
- Review submission (1–5 stars + drink order + auto date)
- Anti-gaming rule: one review per user/shop/day
- Leaderboards:
  - Best shops (Bayesian weighted, min review threshold)
  - Top drinkers
- Filter by drink
- Reviewed-only map option
- CSV export

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py