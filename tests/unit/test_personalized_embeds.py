from __future__ import annotations

from bh_dic.discord.embeds import result_embed
from bh_dic.discord.interactions import InteractionResult, ResultField
from bh_dic.language import BotLanguageProfile


def _result(*, success: bool = True, description: str = "Dati deterministici") -> InteractionResult:
    return InteractionResult(
        title="Esito",
        description=description,
        fields=(ResultField(name="Stato", value="Autorizzato"),),
        correlation_id="corr-safe-123",
        success=success,
    )


def test_embed_default_output_remains_unchanged() -> None:
    result = _result()
    embed = result_embed(result)
    explicit_default = result_embed(result, BotLanguageProfile())

    assert embed.title == "Esito"
    assert embed.description == "Dati deterministici"
    assert embed.fields[0].name == "Stato"
    assert embed.fields[0].value == "Autorizzato"
    assert embed.footer.text == "Correlation ID: corr-safe-123"
    assert not embed.author
    assert explicit_default.to_dict() == embed.to_dict()


def test_embed_applies_only_bounded_local_success_decoration() -> None:
    profile = BotLanguageProfile(
        language="en",
        tone="friendly",
        address_style="tu",
        verbosity="detailed",
        emoji_mode="status",
        display_name="Hotel HR",
        opening="Welcome.",
        closing="Thank you.",
    )

    embed = result_embed(_result(), profile)

    assert embed.author.name == "\N{WHITE HEAVY CHECK MARK} Hotel HR"
    assert embed.title == "Esito"
    assert embed.description == "Welcome.\n\nDati deterministici"
    assert embed.fields[0].value == "Autorizzato"
    assert embed.footer.text == "Thank you. \N{BULLET} Correlation ID: corr-safe-123"


def test_embed_does_not_rephrase_or_decorate_sensitive_error_content() -> None:
    profile = BotLanguageProfile(
        emoji_mode="status",
        display_name="Hotel HR",
        opening="Apertura locale.",
        closing="Chiusura locale.",
    )

    embed = result_embed(
        _result(success=False, description="Operazione non completata."),
        profile,
    )

    assert embed.author.name == "\N{WARNING SIGN} Hotel HR"
    assert embed.title == "Esito"
    assert embed.description == "Operazione non completata."
    assert embed.footer.text == "Correlation ID: corr-safe-123"


def test_embed_omits_opening_instead_of_truncating_deterministic_data() -> None:
    description = "x" * 4_096
    profile = BotLanguageProfile(opening="Buongiorno.")

    embed = result_embed(_result(description=description), profile)

    assert embed.description == description
    assert len(embed.description) == 4_096


def test_embed_omits_all_decorations_when_they_exceed_discord_total_budget() -> None:
    result = InteractionResult(
        title="T" * 256,
        description="D" * 4_096,
        fields=tuple(ResultField("N" * 110, "V" * 400) for _ in range(3)),
        correlation_id="corr-safe-123",
        success=True,
    )
    profile = BotLanguageProfile(
        display_name="Assistente Risorse Umane",
        opening=("Risultato autorizzato. " * 5).strip(),
        closing=("Operazione verificata. " * 5).strip(),
        emoji_mode="status",
    )

    base = result_embed(result)
    decorated = result_embed(result, profile)

    assert len(base) <= 6_000
    assert decorated.to_dict() == base.to_dict()
    assert all(len(field.value) == 400 for field in decorated.fields)


def test_embed_compacts_worst_case_without_hiding_omissions() -> None:
    result = InteractionResult(
        title="T" * 256,
        description="D" * 4_096,
        fields=tuple(
            ResultField(
                f"Dipendente {index:02d} " + "N" * 300,
                f"Dettagli {index:02d} " + "V" * 1_200,
            )
            for index in range(25)
        ),
        correlation_id="corr-" + "C" * 200,
        success=True,
    )
    profile = BotLanguageProfile(
        display_name="Assistente Risorse Umane",
        opening="Ecco il quadro HR completo che mi hai chiesto.",
        closing="Sono qui se vuoi approfondire insieme.",
        emoji_mode="status",
    )

    embed = result_embed(result, profile)

    assert len(embed) <= 6_000
    assert len(embed.fields) == 25
    assert all(field.name and field.value for field in embed.fields)
    assert [field.name[:13] for field in embed.fields] == [
        f"Dipendente {index:02d}" for index in range(25)
    ]
    assert all(field.name.endswith("…") for field in embed.fields)
    assert all(field.value.endswith("…") for field in embed.fields)
    assert "Contenuto ridotto per i limiti Discord." in embed.footer.text
    assert "Correlation ID: corr-" in embed.footer.text
    assert not embed.author
    assert not embed.description.startswith("Apertura")


def test_embed_preserves_error_description_and_correlation_when_compacting() -> None:
    description = "Errore verificato. " + "D" * 4_000
    result = InteractionResult(
        title="Errore operativo",
        description=description,
        fields=tuple(ResultField("Contesto " + "N" * 240, "V" * 1_024) for _ in range(25)),
        correlation_id="corr-safe-123",
        success=False,
    )

    embed = result_embed(result, BotLanguageProfile(emoji_mode="status"))

    assert len(embed) <= 6_000
    assert embed.description == description
    assert len(embed.fields) == 25
    assert all(field.name and field.value for field in embed.fields)
    assert "Contenuto ridotto per i limiti Discord." in embed.footer.text
    assert "Correlation ID: corr-safe-123" in embed.footer.text
    assert not embed.author


def test_embed_reports_fields_omitted_by_discord_count_limit() -> None:
    result = InteractionResult(
        title="Esito",
        description="Dati",
        fields=tuple(ResultField(f"Campo {index}", "valore") for index in range(26)),
    )

    embed = result_embed(result)

    assert len(embed.fields) == 25
    assert embed.footer.text == "Contenuto ridotto per i limiti Discord."
    assert len(embed) <= 6_000
