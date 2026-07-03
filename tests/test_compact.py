from aidag.compact import compact_meanings

RES = [{"alt_id": "utskottet", "text": "x", "source_partier": []},
       {"alt_id": "res-1", "text": "Reservation 1", "source_partier": ["C"]}]


def test_lagforslag():
    c = compact_meanings(
        "Riksdagen antar regeringens förslag till lag om ändring i lagen (1997:238) "
        "om arbetslöshetsförsäkring. Därmed bifaller riksdagen proposition [nr] och avslår motion [nr].",
        RES,
    )
    assert "anta regeringens lagförslag" in c["ja_sv"]
    assert "reservation 1 från C" in c["nej_sv"]
    assert "legislative proposal" in c["ja_en"]


def test_tillkannagivande_topic():
    c = compact_meanings(
        "Riksdagen ställer sig bakom det som utskottet anför om en uppräkning av "
        "schablonbeloppet för assistansersättningen och tillkännager detta för regeringen.",
        [],
    )
    assert "uppmanar regeringen" in c["ja_sv"]
    assert "schablonbeloppet" in c["ja_sv"]
    assert c["nej_sv"] == "Att avvisa utskottets förslag."


def test_avslag_motioner():
    c = compact_meanings("Riksdagen avslår motionerna 2022/23:1 av A och 2022/23:2 av B.", RES)
    assert "avslå motionsyrkandena" in c["ja_sv"]


def test_skrivelse():
    c = compact_meanings("Riksdagen lägger skrivelse 2022/23:30 till handlingarna.", [])
    assert "till handlingarna" in c["ja_sv"]


def test_fallback_is_first_sentence():
    c = compact_meanings("Riksdagen beslutar något ovanligt som reglerna inte känner igen. Andra meningen.", [])
    assert "något ovanligt" in c["ja_sv"]
    assert "Andra meningen" not in c["ja_sv"]
