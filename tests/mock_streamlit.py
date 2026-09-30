"""Minimal stand-in for streamlit so every page function can be executed headless in CI (catches runtime errors in
pandas/styling/logic). Widgets return their defaults; Styler objects are rendered to HTML to execute style fns."""
import sys, types

class Stop(Exception): pass

class SS(dict):
    __getattr__ = lambda s, k: s[k] if k in s else (_ for _ in ()).throw(AttributeError(k))
    def __setattr__(s, k, v): s[k] = v

class M:
    PRESS = set()
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __getattr__(self, name): return _generic
    def __call__(self, *a, **k): return self
    def button(self, label, *a, **k): return label in M.PRESS
    def selectbox(self, label, options, index=0, format_func=str, **k):
        options = list(options); [format_func(o) for o in options]; return options[index] if options else None
    def radio(self, label, options, index=0, **k): return list(options)[index]
    def multiselect(self, label, options, default=None, format_func=str, **k):
        [format_func(o) for o in options]; return list(default or [])
    def checkbox(self, label, value=False, **k): return value
    def number_input(self, label, min_value=None, max_value=None, value=None, step=None, **k): return value if value is not None else (min_value or 0)
    def slider(self, label, min_value=None, max_value=None, value=None, **k): return value
    def text_input(self, *a, **k): return ""
    def text_area(self, *a, **k): return ""
    def chat_input(self, *a, **k): return None
    def file_uploader(self, *a, **k): return None
    def download_button(self, *a, **k): return False
    def columns(self, spec, **k): return [M() for _ in range(spec if isinstance(spec, int) else len(spec))]
    def tabs(self, labels): return [M() for _ in labels]
    def stop(self): raise Stop()
    def rerun(self): raise Stop()
    def dataframe(self, obj, *a, **k):
        if hasattr(obj, "to_html") and obj.__class__.__name__ == "Styler": obj.to_html()
    def plotly_chart(self, fig, *a, **k): fig.to_dict()
    def image(self, img, *a, **k): pass

def _generic(*a, **k): return M()

def install():
    st = M(); mod = types.ModuleType("streamlit")
    for n in dir(M):
        if not n.startswith("__"): setattr(mod, n, getattr(st, n))
    mod.__getattr__ = lambda name: _generic
    mod.session_state = SS(); mod.sidebar = M()
    sys.modules["streamlit"] = mod
    try:
        import plotly  # noqa
    except ImportError:   # sandbox without plotly: fake px that validates column names
        class Fig:
            def update_layout(self, **k): return self
            def to_dict(self): return {}
        def mk(df=None, **k):
            for key in ("x", "y", "color", "names", "values"):
                c = k.get(key)
                if c and hasattr(df, "columns") and c not in df.columns: raise KeyError(f"column {c} not in {list(df.columns)}")
            return Fig()
        px = types.ModuleType("plotly.express")
        for n in ("bar", "line", "scatter", "pie", "imshow", "density_heatmap"): setattr(px, n, mk)
        pl = types.ModuleType("plotly"); pl.express = px
        sys.modules["plotly"] = pl; sys.modules["plotly.express"] = px
    return mod
