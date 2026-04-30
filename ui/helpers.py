import pandas as pd

def full_name(u):
    return f"{u.first_name} {u.last_name}"

def extract_street(address: str):
    if not address or not str(address).strip():
        return "Unknown street"
    return " ".join(str(address).split())

def shop_label(row):
    return f"{row.get('name', 'Unknown cafe')}, {extract_street(row.get('address'))}"

def render_star(x):
    return f"⭐ {round(float(x), 1):.1f}"

def add_last_visit_column(shops_df, reviews_df, user_id):
    out = shops_df.copy()
    if out.empty:
        out["Last visit"] = pd.NaT
        return out
    ur = reviews_df[reviews_df["user_id"] == user_id]
    if ur.empty:
        out["Last visit"] = pd.NaT
        return out
    last_map = ur.groupby("shop_id")["review_date"].max().to_dict()
    out["Last visit"] = out["id"].map(last_map)
    return out

def green_visited_rows(df):
    styles = pd.DataFrame("", index=df.index, columns=df.columns)
    mask = df["Last visit"].notna() & (df["Last visit"].astype(str).str.strip() != "")
    styles.loc[mask, :] = "background-color: #d1fae5"
    return styles