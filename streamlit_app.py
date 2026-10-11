import os
import streamlit as st

st.set_page_config(page_title="Markets Assistant", page_icon="📈", layout="centered")

def secret(key, default=None):
    try:
        return st.secrets[key]
    except Exception:
        return default

# On Streamlit Cloud, copy secrets into env vars BEFORE graph_api is imported.
# Locally, graph_api loads your .env file as before.
for k in ("GROQ_API_KEY", "TAVILY_API_KEY"):
    if secret(k):
        os.environ[k] = secret(k)

from groq import RateLimitError

@st.cache_resource
def load_graph():
    import graph_api
    return graph_api.app

# ---- optional password gate (protects your free API credits) ----
APP_PASSWORD = secret("APP_PASSWORD")
if APP_PASSWORD and not st.session_state.get("authed"):
    st.title("📈 Markets Assistant")
    pw = st.text_input("Access password", type="password")
    if pw:
        if pw == APP_PASSWORD:
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

graph = load_graph()

st.title("📈 Markets Assistant")
st.caption("Indian & global stocks, bullion, forex, mutual funds, IPOs, crude oil and market news.")

with st.sidebar:
    st.subheader("Try asking")
    st.markdown(
        "- Nifty 50 and Sensex today\n"
        "- Gold and silver price in INR\n"
        "- Convert 2500 USD to INR\n"
        "- Latest NAV of Parag Parikh Flexi Cap\n"
        "- Any IPOs opening this week?\n"
        "- Why did the Sensex fall today?\n"
        "- SIP of ₹10,000/month at 12% for 10 years"
    )
    if st.button("Clear chat"):
        st.session_state.messages = []
        st.rerun()
    st.caption("Prices may be delayed and show the last trading day when markets are closed. "
               "News is summarised from headlines. For information only, not investment advice.")

if "messages" not in st.session_state:
    st.session_state.messages = []

def show(text):  # "$" would otherwise be rendered as LaTeX by Streamlit
    st.markdown(text.replace("$", "\\$"))

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        show(m["content"])
        if m.get("data"):
            with st.expander("Data and sources used"):
                st.code(m["data"])

q = st.chat_input("Ask about markets...")
if q:
    history = [(m["role"], m["content"]) for m in st.session_state.messages]
    st.session_state.messages.append({"role": "user", "content": q})
    with st.chat_message("user"):
        show(q)
    with st.chat_message("assistant"):
        with st.spinner("Fetching live data..."):
            try:
                out = graph.invoke({"question": q, "history": history, "retries": 0})
                answer, data = out["answer"], out.get("context", "")
            except RateLimitError:
                answer, data = "The assistant is busy right now. Please try again in a minute.", ""
            except Exception:
                answer, data = "Sorry, something went wrong fetching that. Please try again or rephrase.", ""
        show(answer)
        if data:
            with st.expander("Data and sources used"):
                st.code(data)
    st.session_state.messages.append({"role": "assistant", "content": answer, "data": data})