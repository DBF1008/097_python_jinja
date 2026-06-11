import pytest
from markupsafe import escape

from jinja2 import Environment
from jinja2.exceptions import SecurityError
from jinja2.exceptions import TemplateRuntimeError
from jinja2.exceptions import TemplateSyntaxError
from jinja2.nodes import EvalContext
from jinja2.sandbox import ImmutableSandboxedEnvironment
from jinja2.sandbox import SandboxedEnvironment
from jinja2.sandbox import SandboxViolation
from jinja2.sandbox import unsafe


class PrivateStuff:
    def bar(self):
        return 23

    @unsafe
    def foo(self):
        return 42

    def __repr__(self):
        return "PrivateStuff"


class PublicStuff:
    def bar(self):
        return 23

    def _foo(self):
        return 42

    def __repr__(self):
        return "PublicStuff"


class TestSandbox:
    def test_unsafe(self, env):
        env = SandboxedEnvironment()
        pytest.raises(
            SecurityError, env.from_string("{{ foo.foo() }}").render, foo=PrivateStuff()
        )
        assert env.from_string("{{ foo.bar() }}").render(foo=PrivateStuff()) == "23"

        pytest.raises(
            SecurityError, env.from_string("{{ foo._foo() }}").render, foo=PublicStuff()
        )
        assert env.from_string("{{ foo.bar() }}").render(foo=PublicStuff()) == "23"
        assert env.from_string("{{ foo.__class__ }}").render(foo=42) == ""
        assert env.from_string("{{ foo.func_code }}").render(foo=lambda: None) == ""
        # security error comes from __class__ already.
        pytest.raises(
            SecurityError,
            env.from_string("{{ foo.__class__.__subclasses__() }}").render,
            foo=42,
        )

    def test_immutable_environment(self, env):
        env = ImmutableSandboxedEnvironment()
        pytest.raises(SecurityError, env.from_string("{{ [].append(23) }}").render)
        pytest.raises(SecurityError, env.from_string("{{ [].clear() }}").render)
        pytest.raises(SecurityError, env.from_string("{{ [1].pop() }}").render)
        pytest.raises(SecurityError, env.from_string("{{ {1:2}.clear() }}").render)

    def test_restricted(self, env):
        env = SandboxedEnvironment()
        pytest.raises(
            TemplateSyntaxError,
            env.from_string,
            "{% for item.attribute in seq %}...{% endfor %}",
        )
        pytest.raises(
            TemplateSyntaxError,
            env.from_string,
            "{% for foo, bar.baz in seq %}...{% endfor %}",
        )

    def test_template_data(self, env):
        env = Environment(autoescape=True)
        t = env.from_string(
            "{% macro say_hello(name) %}"
            "<p>Hello {{ name }}!</p>{% endmacro %}"
            '{{ say_hello("<blink>foo</blink>") }}'
        )
        escaped_out = "<p>Hello &lt;blink&gt;foo&lt;/blink&gt;!</p>"
        assert t.render() == escaped_out
        assert str(t.module) == escaped_out
        assert escape(t.module) == escaped_out
        assert t.module.say_hello("<blink>foo</blink>") == escaped_out
        assert (
            escape(t.module.say_hello(EvalContext(env), "<blink>foo</blink>"))
            == escaped_out
        )
        assert escape(t.module.say_hello("<blink>foo</blink>")) == escaped_out

    def test_attr_filter(self, env):
        env = SandboxedEnvironment()
        tmpl = env.from_string('{{ cls|attr("__subclasses__")() }}')
        pytest.raises(SecurityError, tmpl.render, cls=int)

    def test_binary_operator_intercepting(self, env):
        def disable_op(left, right):
            raise TemplateRuntimeError("that operator so does not work")

        for expr, ctx, rv in ("1 + 2", {}, "3"), ("a + 2", {"a": 2}, "4"):
            env = SandboxedEnvironment()
            env.binop_table["+"] = disable_op
            t = env.from_string(f"{{{{ {expr} }}}}")
            assert t.render(ctx) == rv
            env.intercepted_binops = frozenset(["+"])
            t = env.from_string(f"{{{{ {expr} }}}}")
            with pytest.raises(TemplateRuntimeError):
                t.render(ctx)

    def test_unary_operator_intercepting(self, env):
        def disable_op(arg):
            raise TemplateRuntimeError("that operator so does not work")

        for expr, ctx, rv in ("-1", {}, "-1"), ("-a", {"a": 2}, "-2"):
            env = SandboxedEnvironment()
            env.unop_table["-"] = disable_op
            t = env.from_string(f"{{{{ {expr} }}}}")
            assert t.render(ctx) == rv
            env.intercepted_unops = frozenset(["-"])
            t = env.from_string(f"{{{{ {expr} }}}}")
            with pytest.raises(TemplateRuntimeError):
                t.render(ctx)


class TestStringFormat:
    def test_basic_format_safety(self):
        env = SandboxedEnvironment()
        t = env.from_string('{{ "a{0.__class__}b".format(42) }}')
        assert t.render() == "ab"

    def test_basic_format_all_okay(self):
        env = SandboxedEnvironment()
        t = env.from_string('{{ "a{0.foo}b".format({"foo": 42}) }}')
        assert t.render() == "a42b"

    def test_safe_format_safety(self):
        env = SandboxedEnvironment()
        t = env.from_string('{{ ("a{0.__class__}b{1}"|safe).format(42, "<foo>") }}')
        assert t.render() == "ab&lt;foo&gt;"

    def test_safe_format_all_okay(self):
        env = SandboxedEnvironment()
        t = env.from_string('{{ ("a{0.foo}b{1}"|safe).format({"foo": 42}, "<foo>") }}')
        assert t.render() == "a42b&lt;foo&gt;"

    def test_empty_braces_format(self):
        env = SandboxedEnvironment()
        t1 = env.from_string('{{ ("a{}b{}").format("foo", "42")}}')
        t2 = env.from_string('{{ ("a{}b{}"|safe).format(42, "<foo>") }}')
        assert t1.render() == "afoob42"
        assert t2.render() == "a42b&lt;foo&gt;"


class TestStringFormatMap:
    def test_basic_format_safety(self):
        env = SandboxedEnvironment()
        t = env.from_string('{{ "a{x.__class__}b".format_map({"x":42}) }}')
        assert t.render() == "ab"

    def test_basic_format_all_okay(self):
        env = SandboxedEnvironment()
        t = env.from_string('{{ "a{x.foo}b".format_map({"x":{"foo": 42}}) }}')
        assert t.render() == "a42b"

    def test_safe_format_all_okay(self):
        env = SandboxedEnvironment()
        t = env.from_string(
            '{{ ("a{x.foo}b{y}"|safe).format_map({"x":{"foo": 42}, "y":"<foo>"}) }}'
        )
        assert t.render() == "a42b&lt;foo&gt;"

    def test_indirect_call(self):
        def run(value, arg):
            return value.run(arg)

        env = SandboxedEnvironment()
        env.filters["run"] = run
        t = env.from_string(
            """{% set
                ns = namespace(run="{0.__call__.__builtins__[__import__]}".format)
            %}
            {{ ns | run(not_here) }}
            """
        )

        with pytest.raises(SecurityError):
            t.render()

    def test_attr_filter(self) -> None:
        env = SandboxedEnvironment()
        t = env.from_string(
            """{{ "{0.__call__.__builtins__[__import__]}"
                  | attr("format")(not_here) }}"""
        )

        with pytest.raises(SecurityError):
            t.render()


class TestSandboxAudit:
    def test_audit_unsafe_attribute(self):
        events: list[SandboxViolation] = []
        env = SandboxedEnvironment(sandbox_audit=events.append)
        t = env.from_string("{{ foo.__class__ }}")
        result = t.render(foo=42)
        assert result == ""
        assert len(events) == 1
        assert events[0].kind == "unsafe_attribute"
        assert events[0].obj_type == "int"
        assert events[0].attr == "__class__"
        assert events[0].context_name == "foo"

    def test_audit_unsafe_call(self):
        events: list[SandboxViolation] = []
        env = SandboxedEnvironment(sandbox_audit=events.append)
        t = env.from_string("{{ foo.foo() }}")
        with pytest.raises(SecurityError):
            t.render(foo=PrivateStuff())
        assert len(events) == 1
        assert events[0].kind == "unsafe_call"
        assert events[0].obj_type == "method"
        assert events[0].attr == "foo"

    def test_audit_immutable_mutation_list(self):
        events: list[SandboxViolation] = []
        env = ImmutableSandboxedEnvironment(sandbox_audit=events.append)
        with pytest.raises(SecurityError):
            env.from_string("{{ items.append(23) }}").render(items=[1, 2])
        assert len(events) >= 1
        mut_events = [e for e in events if e.kind == "immutable_mutation"]
        assert len(mut_events) == 1
        assert mut_events[0].obj_type == "list"
        assert mut_events[0].attr == "append"
        assert mut_events[0].context_name == "items"

    def test_audit_immutable_mutation_dict(self):
        events: list[SandboxViolation] = []
        env = ImmutableSandboxedEnvironment(sandbox_audit=events.append)
        with pytest.raises(SecurityError):
            env.from_string("{{ d.clear() }}").render(d={"a": 1})
        assert any(
            e.kind == "immutable_mutation" and e.attr == "clear" and e.obj_type == "dict"
            for e in events
        )

    def test_audit_context_name_resolved(self):
        events: list[SandboxViolation] = []
        env = SandboxedEnvironment(sandbox_audit=events.append)
        obj = type("Secret", (), {"__repr__": lambda s: "Secret()"})()
        t = env.from_string("{{ thing.__class__ }}")
        t.render(thing=obj)
        assert len(events) == 1
        assert events[0].context_name == "thing"

    def test_audit_context_name_none_for_nested(self):
        events: list[SandboxViolation] = []
        env = SandboxedEnvironment(sandbox_audit=events.append)
        inner = {"__class__": "nope"}
        t = env.from_string("{{ outer.inner.__class__ }}")
        t.render(outer={"inner": inner})
        attr_events = [e for e in events if e.kind == "unsafe_attribute"]
        if attr_events:
            assert attr_events[0].attr == "__class__"

    def test_no_audit_backward_compatible(self):
        env = SandboxedEnvironment()
        assert env.sandbox_audit is None
        t = env.from_string("{{ foo.__class__ }}")
        assert t.render(foo=42) == ""
        pytest.raises(
            SecurityError,
            env.from_string("{{ foo.foo() }}").render,
            foo=PrivateStuff(),
        )

    def test_no_object_leak(self):
        events: list[SandboxViolation] = []
        env = SandboxedEnvironment(sandbox_audit=events.append)
        secret = type("MySecret", (), {"__repr__": lambda s: "s3cret"})()
        env.from_string("{{ s.__class__ }}").render(s=secret)
        assert len(events) == 1
        v = events[0]
        assert isinstance(v.obj_type, str)
        assert isinstance(v.attr, str)
        assert v.context_name is None or isinstance(v.context_name, str)
        assert not any(
            f is secret for f in (v.kind, v.obj_type, v.attr, v.context_name)
        )

    def test_violation_is_frozen(self):
        events: list[SandboxViolation] = []
        env = SandboxedEnvironment(sandbox_audit=events.append)
        env.from_string("{{ foo.__class__ }}").render(foo=42)
        with pytest.raises(AttributeError):
            events[0].kind = "hacked"  # type: ignore[misc]

    def test_audit_via_getitem(self):
        events: list[SandboxViolation] = []
        env = SandboxedEnvironment(sandbox_audit=events.append)
        t = env.from_string('{{ foo["__class__"] }}')
        t.render(foo=42)
        attr_events = [e for e in events if e.kind == "unsafe_attribute"]
        assert len(attr_events) == 1
        assert attr_events[0].attr == "__class__"
        assert attr_events[0].obj_type == "int"

    def test_audit_multiple_violations(self):
        events: list[SandboxViolation] = []
        env = SandboxedEnvironment(sandbox_audit=events.append)
        t = env.from_string("{{ a.__class__ }}{{ b.__class__ }}")
        t.render(a=42, b="hi")
        assert len(events) == 2
        types_seen = {e.obj_type for e in events}
        assert types_seen == {"int", "str"}

    def test_audit_safe_access_no_event(self):
        events: list[SandboxViolation] = []
        env = SandboxedEnvironment(sandbox_audit=events.append)
        t = env.from_string("{{ foo.bar() }}")
        result = t.render(foo=PublicStuff())
        assert result == "23"
        assert len(events) == 0


class TestSandboxAuditAsync:
    def test_audit_async_unsafe_attribute(self):
        import asyncio

        events: list[SandboxViolation] = []
        env = SandboxedEnvironment(sandbox_audit=events.append, enable_async=True)
        t = env.from_string("{{ foo.__class__ }}")
        result = asyncio.run(t.render_async(foo=42))
        assert result == ""
        assert len(events) == 1
        assert events[0].kind == "unsafe_attribute"
        assert events[0].obj_type == "int"
        assert events[0].attr == "__class__"
        assert events[0].context_name == "foo"

    def test_audit_async_unsafe_call(self):
        import asyncio

        events: list[SandboxViolation] = []
        env = SandboxedEnvironment(sandbox_audit=events.append, enable_async=True)
        t = env.from_string("{{ foo.foo() }}")
        with pytest.raises(SecurityError):
            asyncio.run(t.render_async(foo=PrivateStuff()))
        assert len(events) == 1
        assert events[0].kind == "unsafe_call"

    def test_audit_async_immutable_mutation(self):
        import asyncio

        events: list[SandboxViolation] = []
        env = ImmutableSandboxedEnvironment(
            sandbox_audit=events.append, enable_async=True
        )
        with pytest.raises(SecurityError):
            asyncio.run(
                env.from_string("{{ items.append(1) }}").render_async(items=[1])
            )
        mut_events = [e for e in events if e.kind == "immutable_mutation"]
        assert len(mut_events) == 1
        assert mut_events[0].attr == "append"

    def test_audit_async_consistent_with_sync(self):
        import asyncio

        sync_events: list[SandboxViolation] = []
        async_events: list[SandboxViolation] = []

        template_str = "{{ x.__class__ }}"
        ctx = {"x": 42}

        env_sync = SandboxedEnvironment(sandbox_audit=sync_events.append)
        env_sync.from_string(template_str).render(**ctx)

        env_async = SandboxedEnvironment(
            sandbox_audit=async_events.append, enable_async=True
        )
        asyncio.run(env_async.from_string(template_str).render_async(**ctx))

        assert len(sync_events) == len(async_events)
        for s, a in zip(sync_events, async_events):
            assert s.kind == a.kind
            assert s.obj_type == a.obj_type
            assert s.attr == a.attr
            assert s.context_name == a.context_name
