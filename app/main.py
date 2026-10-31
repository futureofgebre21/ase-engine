"""Streamlit entrypoint. Run with:  streamlit run app/main.py"""

import streamlit as st
from pydantic import ValidationError

from ase.core.config import get_settings
from ase.security.redaction import redact
from ase.services.project_service import ProjectService
from ase.storage.run_store import JsonRunStore
from ase.ui.pages import render_app

st.set_page_config(page_title="ASE", layout="wide")


@st.cache_resource
def _service() -> ProjectService:
    settings = get_settings()
    return ProjectService(settings, JsonRunStore(settings.workspace_root))


try:
    service = _service()
except ValidationError as exc:
    st.error("ASE could not start because the configuration is invalid.")
    st.code(redact(str(exc)))
    st.stop()

render_app(service)
