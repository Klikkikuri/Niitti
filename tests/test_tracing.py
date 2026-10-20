"""
Tests for niitti tracing subpackage.

:purpose: Verify OpenTelemetry tracing setup, crash span dumper, emoji mapping, Sentry integration, and flush/shutdown support.
"""

from concurrent.futures import ThreadPoolExecutor
import sys
import time
from unittest.mock import MagicMock, patch

import pytest
import structlog.contextvars
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import niitti.tracing.crash_buffer as crash_buffer_module
import niitti.tracing.provider as provider_module
from niitti.settings.telemetry import TelemetrySettings
from niitti.tracing import (
    DEFAULT_SPAN_EMOJI,
    activate_tracing,
    clear_crash_span_buffer,
    configure_tracing,
    flush_tracing,
    get_span_emoji,
    setup_crash_span_dumper,
    setup_sentry,
    setup_tracing,
    shutdown_tracing,
    span,
    span_id_to_emoji,
)


def test_get_span_emoji_mapping():
    """
    Verify get_span_emoji matches exact '.' segments and leaf tokens right-to-left against SPAN_EMOJI_MAP.

    :return: None
    """
    assert (
        get_span_emoji("meri.cli.run") == "🏁"
    )  # right-to-left segment check: run -> cli -> meri
    assert get_span_emoji("pipeline.run") == "🏁"
    assert get_span_emoji("fetch.article") == "📰"
    assert get_span_emoji("fetch_source") == "📡"
    assert get_span_emoji("prune_article") == "📰"
    assert get_span_emoji("generate_title") == "✨"
    assert get_span_emoji("meri.cli.run.fetch_source") == "📡"
    assert get_span_emoji("meri") == "🌊"
    assert get_span_emoji("db") == "🗄️"
    assert get_span_emoji("unknown_action_xyz") == DEFAULT_SPAN_EMOJI
    # Substring / unmapped segment boundary tests
    assert get_span_emoji("domain_name") == DEFAULT_SPAN_EMOJI
    assert get_span_emoji("dashboard") == DEFAULT_SPAN_EMOJI
    assert get_span_emoji("failsafe") == DEFAULT_SPAN_EMOJI


def test_span_id_to_emoji_backwards_compatibility():
    """
    Verify span_id_to_emoji returns DEFAULT_SPAN_EMOJI.

    :return: None
    """
    assert span_id_to_emoji(12345) == DEFAULT_SPAN_EMOJI
    assert span_id_to_emoji("abc") == DEFAULT_SPAN_EMOJI
    assert span_id_to_emoji(None) == DEFAULT_SPAN_EMOJI


def test_clear_crash_span_buffer():
    """
    Verify clear_crash_span_buffer clears in-memory crash exporter if present.

    :return: None
    """
    mock_exporter = MagicMock(spec=InMemorySpanExporter)
    crash_buffer_module._crash_span_exporter = mock_exporter

    clear_crash_span_buffer()
    mock_exporter.clear.assert_called_once()

    # Reset
    crash_buffer_module._crash_span_exporter = None


def test_setup_crash_span_dumper_and_excepthook():
    """
    Verify setup_crash_span_dumper configures excepthook and handles crashes cleanly.

    :return: None
    """
    original_excepthook = sys.excepthook
    provider = TracerProvider()

    setup_crash_span_dumper(provider)
    exporter = setup_crash_span_dumper(trace_provider=None)
    assert exporter is not None
    assert sys.excepthook != original_excepthook

    # Test KeyboardInterrupt pass-through
    mock_old_excepthook = MagicMock()
    with patch("sys.excepthook", mock_old_excepthook):
        try:
            sys.excepthook(KeyboardInterrupt, KeyboardInterrupt("interrupted"), None)
        finally:
            sys.excepthook = original_excepthook

    # Test Exception handling output
    try:
        current_hook = sys.excepthook
        with patch("sys.stderr.write") as mock_stderr:
            current_hook(RuntimeError, RuntimeError("test crash"), None)
            assert mock_stderr.called
    finally:
        sys.excepthook = original_excepthook
        crash_buffer_module._crash_span_exporter = None


def test_setup_sentry_disabled():
    """
    Verify setup_sentry does nothing when DSN is empty or None.

    :return: None
    """
    with patch("sentry_sdk.init") as mock_init:
        setup_sentry(dsn=None)
        mock_init.assert_not_called()


def test_setup_sentry_enabled():
    """
    Verify setup_sentry initializes sentry_sdk when DSN is provided.

    :return: None
    """
    with patch("sentry_sdk.init") as mock_init:
        setup_sentry(
            dsn="https://key@sentry.io/123",
            environment="production",
            send_logs=False,
            openai_integration=True,
        )
        mock_init.assert_called_once()
        _, kwargs = mock_init.call_args
        assert kwargs["dsn"] == "https://key@sentry.io/123"
        assert kwargs["environment"] == "production"


def test_setup_tracing_disabled():
    """
    Verify setup_tracing returns None when enabled is False.

    :return: None
    """
    settings = TelemetrySettings(enabled=False)
    result = setup_tracing(settings)
    assert result is None


def test_setup_tracing_enabled_and_idempotent():
    """
    Verify setup_tracing initializes OpenTelemetry tracer idempotently.

    :return: None
    """
    provider_module._active_tracer = None
    provider_module._active_tracer_provider = None

    settings = TelemetrySettings(enabled=True, service_name="test_app")
    tracer1 = setup_tracing(settings)
    assert tracer1 is not None

    # Second invocation returns the exact same tracer without duplicating setup
    tracer2 = setup_tracing(settings)
    assert tracer2 is tracer1


def test_configure_tracing_disabled():
    """
    Verify configure_tracing returns (None, None) when enabled is False.

    :return: None
    """
    settings = TelemetrySettings(enabled=False)
    provider, tracer = configure_tracing(settings)
    assert provider is None
    assert tracer is None


def test_configure_and_activate_tracing_separately():
    """
    Verify configure_tracing instantiates providers without activating, and activate_tracing activates them.

    :return: None
    """
    provider_module._active_tracer = None
    provider_module._active_tracer_provider = None

    settings = TelemetrySettings(enabled=True, service_name="test_sep_app")
    provider, tracer = configure_tracing(settings)
    assert provider is not None
    assert tracer is not None
    # Global active tracer state is not updated yet
    assert provider_module._active_tracer_provider is None
    assert provider_module._active_tracer is None

    # Now activate
    activated_tracer = activate_tracing(provider, tracer)
    assert activated_tracer is tracer
    assert provider_module._active_tracer_provider is provider
    assert provider_module._active_tracer is tracer


def test_flush_and_shutdown_tracing():
    """
    Verify flush_tracing and shutdown_tracing execute cleanly on active tracer provider.

    :return: None
    """
    provider_module._active_tracer = None
    provider_module._active_tracer_provider = None

    settings = TelemetrySettings(enabled=True, service_name="test_flush_app")
    tracer = setup_tracing(settings)
    assert tracer is not None

    # Verify flush returns True
    flush_result = flush_tracing(timeout_millis=1000)
    assert flush_result is True

    # Verify shutdown cleans up active tracer state
    shutdown_tracing(timeout_millis=1000)
    assert provider_module._active_tracer_provider is None
    assert provider_module._active_tracer is None


def test_tracing_missing_service_name_raises_value_error():
    """
    Verify configure_tracing and setup_tracing raise ValueError when service_name is missing.

    :return: None
    """
    settings = TelemetrySettings(enabled=True, service_name=None)
    with pytest.raises(ValueError, match="service_name is required"):
        configure_tracing(settings)

    with pytest.raises(ValueError, match="service_name is required"):
        setup_tracing(settings)


def test_span_decorator_concurrent_invocations():
    """
    Verify @span decorator creates a fresh _SpanContextManager instance per invocation without context corruption.
    Check span IDs, count, attributes, and structlog contextvars span_path thread safety.

    :return: None
    """
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    with patch.object(trace, "_TRACER_PROVIDER", provider):
        captured_span_paths = []

        def worker_task(item_id: int):
            @span(
                f"concurrent_test_task_{item_id}",
                attributes={"test_key": "test_val", "item_id": item_id},
            )
            def run_task():
                ctx = structlog.contextvars.get_contextvars()
                captured_span_paths.append((item_id, ctx.get("span_path")))
                time.sleep(0.01)
                return item_id * 2

            return run_task()

        num_tasks = 10
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(worker_task, i) for i in range(num_tasks)]
            results = [f.result() for f in futures]

        assert results == [i * 2 for i in range(num_tasks)]

        # Verify structlog span_path was accurately captured in all thread invocations without cross-task leakage
        assert len(captured_span_paths) == num_tasks
        for item_id, path in captured_span_paths:
            assert path == f"concurrent_test_task_{item_id}"

        # Verify OTel finished spans
        finished_spans = exporter.get_finished_spans()
        assert len(finished_spans) == num_tasks

        # Verify every span has a distinct span_id and correct per-task attributes
        unique_span_ids = {
            sc.span_id
            for s in finished_spans
            if (sc := s.get_span_context()) is not None
        }
        assert len(unique_span_ids) == num_tasks

        for s in finished_spans:
            assert s.attributes is not None
            item_id = s.attributes["item_id"]
            expected_name = f"concurrent_test_task_{item_id}"
            assert s.name == expected_name
            assert s.attributes["test_key"] == "test_val"
            assert s.attributes["span_path"] == expected_name


def test_span_records_and_reraises_exception():
    """
    Verify span context manager and decorator record exception, set ERROR status, and reraise exception.

    :return: None
    """
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with patch.object(trace, "_TRACER_PROVIDER", provider):
        with pytest.raises(ValueError, match="boom"):
            with span("fails"):
                raise ValueError("boom")

        finished = exporter.get_finished_spans()
        assert len(finished) == 1
        assert finished[0].status.status_code == trace.StatusCode.ERROR
        assert len(finished[0].events) > 0  # exception recorded

        exporter.clear()

        @span("decorator_fails")
        def failing_func():
            raise RuntimeError("decorator boom")

        with pytest.raises(RuntimeError, match="decorator boom"):
            failing_func()

        finished_dec = exporter.get_finished_spans()
        assert len(finished_dec) == 1
        assert finished_dec[0].status.status_code == trace.StatusCode.ERROR
        assert len(finished_dec[0].events) > 0


def test_span_context_manager_as_binding_returns_usable_span():
    """
    Verify `with span(...) as s:` yields a usable trace.Span instance permitting set_attribute calls.

    :return: None
    """
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    with patch.object(trace, "_TRACER_PROVIDER", provider):
        with span("test_span_binding") as s:
            assert isinstance(s, trace.Span)
            s.set_attribute("prune_reason", "test_reason")

        finished = exporter.get_finished_spans()
        assert len(finished) == 1
        assert finished[0].name == "test_span_binding"
        assert finished[0].attributes is not None
        assert finished[0].attributes.get("prune_reason") == "test_reason"


def test_crash_span_dumper_uses_span_path_for_emoji(capsys: pytest.CaptureFixture[str]):
    """
    Verify crash span dumper derives emoji from attributes['span_path'] rather than span.name.

    :return: None
    """
    original_excepthook = sys.excepthook
    crash_buffer_module._crash_span_exporter = None
    provider = TracerProvider()
    exporter = setup_crash_span_dumper(provider)
    assert exporter is not None

    try:
        with patch.object(trace, "_TRACER_PROVIDER", provider):
            with span("unmapped_leaf", attributes={"span_path": "meri.cli.run"}):
                pass

            assert len(exporter.get_finished_spans()) == 1
            sys.excepthook(RuntimeError, RuntimeError("test crash for span_path emoji"), None)

        captured = capsys.readouterr()
        err_output = captured.err

        assert "🏁" in err_output
        assert "unmapped_leaf" in err_output
    finally:
        sys.excepthook = original_excepthook
        crash_buffer_module._crash_span_exporter = None


LONG_HTML = "<html>" + "<div>x</div>\n" * 2000 + "</html>"


def _crash_with_spans(body, exc_type=RuntimeError):
    """
    Run body inside a fresh provider, call the crash hook with the exception that body raises, and return nothing.

    :param body: Callable that opens spans and raises exc_type.
    """
    original_excepthook = sys.excepthook
    crash_buffer_module._crash_span_exporter = None
    provider = TracerProvider()
    setup_crash_span_dumper(provider)
    try:
        with patch.object(trace, "_TRACER_PROVIDER", provider):
            try:
                body()
            except exc_type as exc:
                sys.excepthook(type(exc), exc, exc.__traceback__)
    finally:
        sys.excepthook = original_excepthook
        crash_buffer_module._crash_span_exporter = None


def test_crash_span_dumper_shows_only_active_branch(capsys: pytest.CaptureFixture[str]):
    """
    Verify the crash dump keeps the failing span and its ancestors, and drops siblings and handled errors.
    """

    def body():
        with span("root_span"):
            with span("ok_sibling"):
                pass
            try:
                with span("stale_failure"):
                    raise ValueError("handled earlier")
            except ValueError:
                pass
            with span("failing_child"):
                raise RuntimeError("crash")

    _crash_with_spans(body)
    err = capsys.readouterr().err

    assert "root_span" in err
    assert "failing_child" in err
    assert "ok_sibling" not in err
    assert "stale_failure" not in err


def test_crash_span_dumper_falls_back_to_last_span(capsys: pytest.CaptureFixture[str]):
    """
    Verify the dump shows the last finished span and a note when no span recorded the exception.
    """

    def body():
        with span("first_span"):
            pass
        with span("last_span"):
            pass
        raise RuntimeError("outside every span")

    _crash_with_spans(body)
    err = capsys.readouterr().err

    assert "last_span" in err
    assert "first_span" not in err
    assert "No span matched the exception" in err


SHORT_VALUE = "short value"
EXACT_VALUE = "e" * crash_buffer_module.MAX_VALUE_LENGTH


@pytest.mark.parametrize(
    ("value", "shortened"),
    [(SHORT_VALUE, False), (EXACT_VALUE, False), (LONG_HTML, True)],
    ids=["short", "exact-limit", "long-html"],
)
def test_crash_span_dumper_shortens_long_values(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, value: str, shortened: bool
):
    """
    Verify values over the limit show head, tail and length, and other values show in full.
    """
    monkeypatch.setenv("COLUMNS", "1000")  # a narrow console would wrap the value

    def body():
        with span("failing_span", attributes={"payload": value}):
            raise RuntimeError("crash")

    _crash_with_spans(body)
    err = " ".join(capsys.readouterr().err.split())

    if shortened:
        assert "<html><div>x</div>" in err
        assert f"({len(value)} chars)" in err
        assert " ".join(value.split()) not in err
    else:
        assert value in err
        assert "chars)" not in err


def test_crash_span_dumper_escapes_rich_markup(capsys: pytest.CaptureFixture[str]):
    """
    Verify bracket text in a value does not break the rich output.
    """

    def body():
        with span("failing_span", attributes={"payload": "[bold]x[/bold] [/oops]"}):
            raise RuntimeError("crash")

    _crash_with_spans(body)
    err = capsys.readouterr().err

    assert "[/oops]" in err
    assert "Error in sys.excepthook" not in err


def test_crash_span_dumper_survives_failing_value():
    """
    Verify the original excepthook still runs when a value cannot be rendered.
    """

    original_excepthook = sys.excepthook
    previous_hook = MagicMock()
    sys.excepthook = previous_hook
    try:
        with patch.object(crash_buffer_module, "_shorten", side_effect=RuntimeError("render")):
            crash_buffer_module._crash_span_exporter = None
            provider = TracerProvider()
            setup_crash_span_dumper(provider)
            with patch.object(trace, "_TRACER_PROVIDER", provider):
                try:
                    with span("failing_span", attributes={"payload": "ok"}):
                        raise RuntimeError("crash")
                except RuntimeError as exc:
                    sys.excepthook(type(exc), exc, exc.__traceback__)
        previous_hook.assert_called_once()
    finally:
        sys.excepthook = original_excepthook
        crash_buffer_module._crash_span_exporter = None


def test_crash_span_dumper_plain_output_shortens_values(capsys: pytest.CaptureFixture[str]):
    """
    Verify the output without rich shows shortened attributes and events.
    """

    def body():
        with span("failing_span", attributes={"payload": LONG_HTML}):
            raise RuntimeError("crash")

    with patch.dict(sys.modules, {"rich": None}):
        _crash_with_spans(body)
    err = capsys.readouterr().err

    assert "APPLICATION CRASHED" in err
    assert "failing_span" in err
    assert "exception: exception.type=RuntimeError" in err
    assert f"({len(LONG_HTML)} chars)" in err
    assert "exception.stacktrace" not in err
