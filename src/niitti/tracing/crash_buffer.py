"""
Crash span ring buffer and waterfall error dumper.
"""

import sys
from collections import defaultdict
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from niitti.tracing.exporters import DEFAULT_CRASH_SPAN_BUFFER_SIZE, RingBufferSpanExporter
from niitti.tracing.instrumentation import get_span_emoji


def _span_id(span: ReadableSpan) -> int | None:
    ctx = span.get_span_context() if hasattr(span, "get_span_context") else None
    return ctx.span_id if ctx else None


def _parent_span_id(span: ReadableSpan) -> int | None:
    parent = getattr(span, "parent", None)
    return parent.span_id if parent is not None else None


def _trace_id(span: ReadableSpan) -> int | None:
    ctx = span.get_span_context() if hasattr(span, "get_span_context") else None
    return ctx.trace_id if ctx else None


def build_span_forest(
    spans,
) -> list[tuple[int | None, list[tuple[ReadableSpan, list]]]]:
    """
    Reconstruct parent/child span hierarchy from a flat list of finished spans.

    Groups spans by trace_id (a crash buffer may contain spans from more than one
    trace), and within each trace links children to parents via span_id /
    parent_span_id. A span whose parent isn't present in the buffer (e.g. the
    parent finished and was evicted from the ring buffer, or the parent belongs
    to a different process) is treated as a root for display purposes.

    :param spans: Iterable of finished ReadableSpan objects.
    :return: List of (trace_id, roots) tuples, where each root is a
        (span, children) tuple and children is recursively the same
        (span, children) list structure, in the original span order.
    """
    spans_by_trace: dict[int | None, list[ReadableSpan]] = defaultdict(list)
    for span in spans:
        spans_by_trace[_trace_id(span)].append(span)

    forest: list[tuple[int | None, list[tuple[ReadableSpan, list]]]] = []

    for trace_id_value, trace_spans in spans_by_trace.items():
        by_id: dict[int, ReadableSpan] = {}
        for span in trace_spans:
            sid = _span_id(span)
            if sid is not None:
                by_id[sid] = span

        children_of: dict[int, list[ReadableSpan]] = defaultdict(list)
        roots: list[ReadableSpan] = []
        for span in trace_spans:
            pid = _parent_span_id(span)
            if pid is not None and pid in by_id:
                children_of[pid].append(span)
            else:
                roots.append(span)

        def attach(span: ReadableSpan) -> tuple[ReadableSpan, list]:
            sid = _span_id(span)
            child_spans = children_of.get(sid, []) if sid is not None else []
            return (span, [attach(child) for child in child_spans])

        forest.append((trace_id_value, [attach(root) for root in roots]))

    return forest


MAX_VALUE_LENGTH = 200
_STACKTRACE_KEY = "exception.stacktrace"
_NO_MATCH_NOTE = "No span matched the exception; showing the last finished span."


def _shorten(value: object) -> str:
    """
    Render a value on one line and cut the middle of long values.

    Whitespace runs collapse to one space so that multi-line values (for example HTML) stay on one tree row.
    """
    raw = str(value)
    text = " ".join(raw.split())
    if len(text) <= MAX_VALUE_LENGTH:
        return text
    half = MAX_VALUE_LENGTH // 2
    return f"{text[:half]} ... {text[-half:]} ({len(raw)} chars)"


def _exception_type_name(exc: BaseException) -> str:
    # Same format as the OpenTelemetry SDK writes to the "exception.type" event attribute.
    cls = type(exc)
    module = cls.__module__
    return cls.__qualname__ if module in ("builtins", "") else f"{module}.{cls.__qualname__}"


def _exception_signatures(exc_value: BaseException | None) -> set[tuple[str, str]]:
    """
    Collect (type, message) of an exception and of its __cause__ / __context__ chain.
    """
    signatures: set[tuple[str, str]] = set()
    seen: set[int] = set()
    current = exc_value
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        signatures.add((_exception_type_name(current), str(current)))
        current = current.__cause__ or current.__context__
    return signatures


def _records_exception(span: ReadableSpan, signatures: set[tuple[str, str]]) -> bool:
    for event in span.events or ():
        if event.name != "exception":
            continue
        attributes = event.attributes or {}
        if (attributes.get("exception.type"), attributes.get("exception.message")) in signatures:
            return True
    return False


def _with_ancestors(spans, targets: list[ReadableSpan]) -> list[ReadableSpan]:
    """
    Return the target spans and their ancestors, in the original span order.
    """
    by_key = {(_trace_id(s), _span_id(s)): s for s in spans}
    keep: set[tuple[int | None, int | None]] = set()
    for target in targets:
        current: ReadableSpan | None = target
        while current is not None:
            key = (_trace_id(current), _span_id(current))
            if key in keep:
                break
            keep.add(key)
            parent_id = _parent_span_id(current)
            current = by_key.get((key[0], parent_id)) if parent_id is not None else None
    return [s for s in spans if (_trace_id(s), _span_id(s)) in keep]


def _prune_to_active_branch(spans, exc_value: BaseException | None):
    """
    Keep only the spans the crashing exception went through, and their ancestors.

    A span matches when one of its "exception" events has the type and message of the exception or of its cause chain. Matching on the exception, and not on the ERROR status, drops spans that failed and were handled earlier.
    If no span matches, keep the last finished span and its ancestors.

    :param spans: Finished spans from the ring buffer.
    :param exc_value: The exception that reached the excepthook.
    :return: (forest, note) where forest comes from build_span_forest and note is None or a text for the reader.
    """
    spans = list(spans)
    signatures = _exception_signatures(exc_value)
    matched = [s for s in spans if _records_exception(s, signatures)]
    note = None
    if not matched:
        if not spans:
            return [], None
        matched = [max(spans, key=lambda s: s.end_time or 0)]
        note = _NO_MATCH_NOTE
    return build_span_forest(_with_ancestors(spans, matched)), note


def _span_label(span: ReadableSpan) -> tuple[str, str, str, float]:
    """
    :return: (emoji, name, status, duration in ms) of a span.
    """
    duration_ms = (span.end_time - span.start_time) / 1e6 if (span.end_time and span.start_time) else 0
    status = span.status.status_code.name if span.status else "UNSET"
    span_ctx = span.get_span_context() if hasattr(span, "get_span_context") else None
    span_path = (
        str(span.attributes.get("span_path")) if (span.attributes and "span_path" in span.attributes) else span.name
    )
    emoji = get_span_emoji(span_path, span_ctx.span_id if span_ctx else None)
    return emoji, span.name, status, duration_ms


def _event_rows(event) -> list[tuple[str, str]]:
    return [(k, _shorten(v)) for k, v in (event.attributes or {}).items() if k != _STACKTRACE_KEY]


# In-memory ring buffer for crash span dumping
_crash_span_exporter: RingBufferSpanExporter | None = None


def clear_crash_span_buffer() -> None:
    """
    Clear the crash span ring buffer.

    Kept for backwards compatibility. Since the buffer is now a bounded ring
    buffer (see RingBufferSpanExporter), calling this is no longer required
    to prevent unbounded memory growth in long-running daemons; it remains
    useful if you want to explicitly reset the buffer between iterations
    (e.g. to avoid showing spans from a prior loop iteration on crash).
    """
    global _crash_span_exporter
    if _crash_span_exporter is not None:
        _crash_span_exporter.clear()


def setup_crash_span_dumper(
    trace_provider: TracerProvider | None = None,
    max_spans: int = DEFAULT_CRASH_SPAN_BUFFER_SIZE,
) -> RingBufferSpanExporter | None:
    """
    Attach a SimpleSpanProcessor + bounded RingBufferSpanExporter and install a
    sys.excepthook to dump active trace spans to console if the application crashes.

    :param trace_provider: TracerProvider to attach the crash span processor to.
    :param max_spans: Maximum number of finished spans retained for crash dumping.
        Older spans are evicted automatically once this cap is reached, so this
        buffer is safe to use in long-running daemons without manual clearing.
    :return: Active RingBufferSpanExporter instance.
    """
    global _crash_span_exporter
    if _crash_span_exporter is not None and trace_provider is None:
        return _crash_span_exporter

    if trace_provider is None:
        provider = trace.get_tracer_provider()
        if isinstance(provider, TracerProvider):
            trace_provider = provider
        elif isinstance(getattr(provider, "_tracer_provider", None), TracerProvider):
            trace_provider = getattr(provider, "_tracer_provider")

    if _crash_span_exporter is None:
        _crash_span_exporter = RingBufferSpanExporter(maxlen=max_spans)

    if trace_provider is not None and _crash_span_exporter is not None:
        already_registered = False
        if hasattr(trace_provider, "_active_span_processor"):
            active_processor = getattr(trace_provider, "_active_span_processor")
            if hasattr(active_processor, "_span_processors"):
                for proc in active_processor._span_processors:
                    if (
                        isinstance(proc, SimpleSpanProcessor)
                        and getattr(proc, "span_exporter", None) is _crash_span_exporter
                    ):
                        already_registered = True
                        break

        if not already_registered:
            trace_provider.add_span_processor(SimpleSpanProcessor(_crash_span_exporter))

    old_excepthook = sys.excepthook

    def dump_rich(spans, exc_type, exc_value, exc_tb) -> None:
        from rich.console import Console
        from rich.markup import escape
        from rich.traceback import Traceback
        from rich.tree import Tree

        current_span = trace.get_current_span()
        console = Console(stderr=True)
        tree = Tree("💥 [bold red]Application Crash - OpenTelemetry Span Waterfall[/bold red]")

        if current_span.is_recording():
            ctx = current_span.get_span_context()
            tree.add(f"[bold yellow]Trace ID:[/bold yellow] {ctx.trace_id:032x}")
            tree.add(f"[bold yellow]Span ID:[/bold yellow]  {ctx.span_id:016x}")

        if spans:
            buffer_note = f" (ring buffer, max {_crash_span_exporter.maxlen})" if _crash_span_exporter else ""
            span_node = tree.add(f"[bold cyan]Recorded Spans ({len(spans)}){buffer_note}[/bold cyan]")

            def add_rich_node(parent_node, span: ReadableSpan, children: list) -> None:
                emoji, name, status, duration_ms = _span_label(span)
                node = parent_node.add(
                    f"{emoji} [bold white]{escape(name)}[/bold white] (status: [bold]{status}[/bold], duration: {duration_ms:.2f}ms)"
                )
                if span.attributes:
                    attr_node = node.add("[dim]Attributes:[/dim]")
                    for k, v in span.attributes.items():
                        attr_node.add(f"[cyan]{escape(str(k))}:[/cyan] {escape(_shorten(v))}")
                if span.events:
                    event_node = node.add("[dim]Events:[/dim]")
                    for ev in span.events:
                        rows = ", ".join(f"{escape(k)}={escape(v)}" for k, v in _event_rows(ev))
                        event_node.add(f"[magenta]{escape(ev.name)}:[/magenta] {rows}")
                for child_span, child_children in children:
                    add_rich_node(node, child_span, child_children)

            forest, note = _prune_to_active_branch(spans, exc_value)
            if note:
                span_node.add(f"[dim]{note}[/dim]")
            for trace_id_value, roots in forest:
                trace_label = f"{trace_id_value:032x}" if trace_id_value is not None else "unknown"
                trace_node = (
                    span_node.add(f"[bold yellow]Trace {trace_label}[/bold yellow]") if len(forest) > 1 else span_node
                )
                for root_span, root_children in roots:
                    add_rich_node(trace_node, root_span, root_children)
        else:
            tree.add("[dim]No finished spans recorded in buffer.[/dim]")

        console.print()
        console.print(tree)
        console.print()
        console.print(Traceback.from_exception(exc_type, exc_value, exc_tb))

    def dump_plain(spans, exc_value) -> None:
        current_span = trace.get_current_span()
        sys.stderr.write("\n" + "=" * 80 + "\n")
        sys.stderr.write("💥 APPLICATION CRASHED - OPENTELEMETRY SPAN TRACE DUMP\n")
        sys.stderr.write("=" * 80 + "\n")
        if current_span.is_recording():
            ctx = current_span.get_span_context()
            sys.stderr.write(f"Trace ID: {ctx.trace_id:032x}\n")
            sys.stderr.write(f"Span ID:  {ctx.span_id:016x}\n")
            sys.stderr.write("-" * 80 + "\n")

        if spans:
            sys.stderr.write(f"Recorded Spans ({len(spans)}):\n")

            def write_plain_node(span: ReadableSpan, children: list, depth: int) -> None:
                indent = "  " * depth
                branch = "└─ " if depth > 0 else ""
                emoji, name, status, duration_ms = _span_label(span)
                sys.stderr.write(f"{indent}{branch}{emoji} {name} (status: {status}, duration: {duration_ms:.2f}ms)\n")
                for k, v in (span.attributes or {}).items():
                    sys.stderr.write(f"{indent}       - {k}: {_shorten(v)}\n")
                for ev in span.events or ():
                    rows = ", ".join(f"{k}={v}" for k, v in _event_rows(ev))
                    sys.stderr.write(f"{indent}       * {ev.name}: {rows}\n")
                for child_span, child_children in children:
                    write_plain_node(child_span, child_children, depth + 1)

            forest, note = _prune_to_active_branch(spans, exc_value)
            if note:
                sys.stderr.write(f"{note}\n")
            for trace_id_value, roots in forest:
                if len(forest) > 1:
                    trace_label = f"{trace_id_value:032x}" if trace_id_value is not None else "unknown"
                    sys.stderr.write(f"Trace {trace_label}:\n")
                for root_span, root_children in roots:
                    write_plain_node(root_span, root_children, 0)
        sys.stderr.write("=" * 80 + "\n\n")

    def crash_excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            old_excepthook(exc_type, exc_value, exc_tb)
            return

        spans = _crash_span_exporter.get_finished_spans() if _crash_span_exporter else ()
        try:
            try:
                dump_rich(spans, exc_type, exc_value, exc_tb)
            except ImportError:
                dump_plain(spans, exc_value)
        except Exception:
            # Fail-soft boundary: a rendering error must not hide the original crash, which old_excepthook reports.
            pass
        old_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = crash_excepthook
    return _crash_span_exporter
