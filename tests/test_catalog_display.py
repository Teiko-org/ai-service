from app.tools.writes.catalog_display import (
    aggregate_fornada_products,
    exclusivo_display_label,
    format_display_token,
    massa_display_label,
)


def test_format_display_token_snake_case():
    assert format_display_token("cacau_expresso") == "Cacau Expresso"


def test_exclusivo_display_label_shows_combo():
    label = exclusivo_display_label(
        {
            "nome": "Giovanna",
            "sabor1": "brigadeiro_tradicional",
            "sabor2": "ninho",
        }
    )
    assert label == "Giovanna (Brigadeiro Tradicional + Ninho)"


def test_massa_display_label():
    assert massa_display_label({"sabor": "red_velvet"}) == "Red Velvet"


def test_aggregate_fornada_products_sums_same_name():
    rows = [
        {"produto": "Pao Frances", "categoria": "Padaria", "quantidade": 5},
        {"produto": "Croissant", "categoria": "Salgados", "quantidade": 3},
        {"produto": "Pao Frances", "categoria": "Padaria", "quantidade": 1},
        {"produto": "Croissant", "categoria": "Salgados", "quantidade": 3},
    ]
    assert aggregate_fornada_products(rows) == [
        ("Croissant (Salgados)", 6),
        ("Pao Frances (Padaria)", 6),
    ]
