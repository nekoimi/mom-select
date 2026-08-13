from mom_select.portfolio import load_holdings


def test_empty_portfolio_file_means_no_holdings(tmp_path):
    path = tmp_path / "portfolio.csv"
    path.touch()

    assert load_holdings(path) == []


def test_header_only_portfolio_file_means_no_holdings(tmp_path):
    path = tmp_path / "portfolio.csv"
    path.write_text("code,name,amount,avg_cost\n", encoding="utf-8")

    assert load_holdings(path) == []
