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
