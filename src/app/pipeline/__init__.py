__all__ = ["run_pipeline"]


def __getattr__(name):
    if name == "run_pipeline":
        from pipeline.processor import run_pipeline

        return run_pipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
