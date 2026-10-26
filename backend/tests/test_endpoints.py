import pandas as pd
from main import analysis_store


def test_ingesta_enriquece_el_dataframe(client, analysis_id):
    df = analysis_store[analysis_id]["dataframe"]
    assert {"_level", "_reason", "_suspicious"} <= set(df.columns)
    assert pd.api.types.is_datetime64_any_dtype(df["Timestamp"])


def test_columnas_derivadas_no_filtran_en_rows(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/rows", params={"page_size": 50}).json()
    assert all(not c.startswith("_") for c in res["columns"])
    assert {"risk_level", "risk_reason", "suspicious"} <= set(res["rows"][0])
