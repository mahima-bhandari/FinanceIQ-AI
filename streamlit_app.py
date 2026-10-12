import os
import time
import uuid

import streamlit as st

st.set_page_config(page_title="Markets Assistant", page_icon="📈", layout="centered")


def secret(key, default=None):
    try:
        return st.secrets[key]
    except Exception:
        return default


# On Streamlit Cloud, copy secrets into env vars BEFORE graph_api is imported.
# Locally, graph_api loads your .env file as before.
for _k in ("GROQ_API_KEY", "TAVILY_API_KEY"):
    if secret(_k):
        os.environ[_k] = secret(_k)

from groq import RateLimitError  # noqa: E402

import storage  # noqa: E402
from links import DIRECTORY  # noqa: E402


@st.cache_resource
def load_graph():
    import graph_api
    return graph_api.app


# ------------------------------------------------------------------ optional password gate
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

# ------------------------------------------------------------------ visitor id (kept in the URL so refresh keeps history)
uid = st.query_params.get("u") or st.session_state.get("uid") or uuid.uuid4().hex[:16]
st.session_state.uid = uid
if st.query_params.get("u") != uid:
    st.query_params["u"] = uid

# ------------------------------------------------------------------ state
if "chats" not in st.session_state:
    loaded = storage.load(uid)
    st.session_state.chats = loaded
    st.session_state.current = (
        max(loaded, key=lambda k: loaded[k].get("updated", 0)) if loaded else None
    )
    st.session_state.confirm_clear = False


def _save():
    storage.save(st.session_state.uid, st.session_state.chats)


# Button callbacks run BEFORE the page reruns, which is why they are reliable.
def new_chat():
    st.session_state.current = None
    st.session_state.confirm_clear = False


def open_chat(cid):
    st.session_state.current = cid
    st.session_state.confirm_clear = False


def delete_chat(cid):
    st.session_state.chats.pop(cid, None)
    if st.session_state.current == cid:
        st.session_state.current = None
    _save()


def ask_clear():
    st.session_state.confirm_clear = True


def cancel_clear():
    st.session_state.confirm_clear = False


def clear_all():
    st.session_state.chats = {}
    st.session_state.current = None
    st.session_state.confirm_clear = False
    _save()


# ------------------------------------------------------------------ helpers
def esc(text):  # "$" would otherwise be rendered as LaTeX by Streamlit
    return str(text).replace("$", "\\$")


def clean_title(t):
    return esc(str(t).replace("[", "(").replace("]", ")"))


def render_message(m):
    with st.chat_message(m["role"]):
        st.markdown(esc(m["content"]))
        if m["role"] != "assistant":
            return
        if m.get("sources"):
            st.markdown("**Sources**")
            st.markdown("\n".join(
                f"- [{s['n']}] [{clean_title(s['title'])}]({s['url']})" for s in m["sources"]))
        if m.get("links"):
            st.markdown("🔗 **Explore directly:** " + " · ".join(
                f"[{clean_title(label)}]({url})" for label, url in m["links"]))
        if m.get("data"):
            with st.expander("Data used"):
                st.code(m["data"])


# ------------------------------------------------------------------ sidebar: chat history
chats = st.session_state.chats
with st.sidebar:
    st.button("➕ New chat", on_click=new_chat, key="new_sidebar")
    st.subheader("Chat history")
    if not chats:
        st.caption("No saved chats yet.")
    for cid, c in sorted(chats.items(), key=lambda kv: kv[1].get("updated", 0), reverse=True):
        col_a, col_b = st.columns([5, 1])
        col_a.button(
            c.get("title", "Chat"), key=f"open_{cid}", on_click=open_chat, args=(cid,),
            type="primary" if cid == st.session_state.current else "secondary",
        )
        col_b.button("🗑", key=f"del_{cid}", on_click=delete_chat, args=(cid,), help="Delete this chat")
    if chats:
        st.divider()
        if st.session_state.confirm_clear:
            st.warning("Delete ALL saved chats?")
            col_y, col_n = st.columns(2)
            col_y.button("Yes, delete", on_click=clear_all, key="clear_yes")
            col_n.button("Cancel", on_click=cancel_clear, key="clear_no")
        else:
            st.button("🗑 Clear all history", on_click=ask_clear, key="clear_ask")
    st.caption("Prices may be delayed and show the last trading day when markets are closed. "
               "News is summarised from headlines. For information only, not investment advice.")

# ------------------------------------------------------------------ main page
st.title("📈 Markets Assistant")
st.caption("Indian & global stocks, bullion, forex, mutual funds, IPOs, crude oil and market news.")
st.button("➕ New chat", on_click=new_chat, key="new_main")

with st.expander("🔗 Direct links to official sources (NSE, BSE, Groww, Zerodha, RBI, MCX, ET, Mint...)"):
    st.caption("Many of these sites block automated access or need a login, so open them directly. "
               "Links open in a new tab. Bloomberg and parts of ET/Mint are paywalled; Zerodha Kite needs a login.")
    for category, items in DIRECTORY.items():
        st.markdown(f"**{category}**  \n" + " · ".join(f"[{name}]({url})" for name, url in items))

cid = st.session_state.current
messages = chats[cid]["messages"] if cid in chats else []

if not messages:
    st.info("Try: Nifty 50 today · Gold price in INR · Convert 2500 USD to INR · Any IPOs opening this week? · "
            "SIP of ₹10,000/month at 12% for 10 years.  Chat history is in the sidebar "
            "(on a phone, tap the › at the top left).")

for m in messages:
    render_message(m)

# ------------------------------------------------------------------ chat input
q = st.chat_input("Ask about markets...")
if q:
    if cid is None or cid not in chats:
        cid = uuid.uuid4().hex[:8]
        title = q.strip().replace("\n", " ")
        chats[cid] = {
            "title": (title[:30] + "…") if len(title) > 30 else (title or "New chat"),
            "created": time.time(), "updated": time.time(), "messages": [],
        }
        st.session_state.current = cid
    chat = chats[cid]
    history = [(m["role"], m["content"]) for m in chat["messages"]]
    chat["messages"].append({"role": "user", "content": q})
    with st.chat_message("user"):
        st.markdown(esc(q))
    with st.chat_message("assistant"):
        with st.spinner("Fetching live data..."):
            try:
                out = graph.invoke({"question": q, "history": history, "retries": 0})
                
                ## TEMPORARY:
                if os.getenv("DEBUG") == "1":
                    st.write("Plan:", out["plan"].model_dump())
                    st.write("Fetched context:", out.get("context"))
                    st.write("Sources:", out.get("sources"))
                    st.write("Cited sources:", out.get("cited"))
                
                reply = {"role": "assistant", "content": out["answer"], "data": out.get("context", ""),
                         "sources": out.get("cited", []), "links": out.get("links", [])}
            except RateLimitError:
                reply = {"role": "assistant", "content": "The assistant is busy right now. Please try again in a minute."}
            except Exception:
                reply = {"role": "assistant", "content": "Sorry, something went wrong fetching that. Please try again or rephrase."}
    chat["messages"].append(reply)
    chat["updated"] = time.time()
    _save()
    st.rerun()  # redraw so the sidebar list and the new answer both come from saved state
