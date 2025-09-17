import streamlit as st
import pandas as pd
import json
import copy
import io
from datetime import datetime

# ──────────────────────────────
# 🔐 PASSWORD PROTECTION
# ──────────────────────────────
def require_login():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.markdown("### 🔐 Secured Access")
        password = st.text_input("Enter app password", type="password")

        if password == st.secrets["app_password"]:
            st.session_state.authenticated = True
            st.success("✅ Access granted. Reloading...")
            st.rerun()
        elif password:
            st.error("❌ Incorrect password")

        st.stop()

require_login()

# ──────────────────────────────
# 🎨 PAGE CONFIG + HEADER
# ──────────────────────────────
st.set_page_config(page_title="MAT Survey Automation Tool", layout="centered")

col1, col2 = st.columns([1, 4], gap="small")
with col1:
    st.image("bain_logo.png", width=140)
with col2:
    st.markdown("## 📝 MAT Survey Automation Tool")

st.markdown(
    """
**Instructions**

1) Upload an **Excel file** and a **base QSF** file.  
2) Click **“Generate Updated QSF”** to apply edits and download the result.
"""
)

# ──────────────────────────────
# 📤 FILE UPLOADS
# ──────────────────────────────
excel_file = st.file_uploader("📄 Excel mapping file (.xlsx)", type=["xlsx"])
qsf_file   = st.file_uploader("📁 Base QSF file (.qsf)", type=["qsf", "json"])
process_btn = st.button("🚀 Generate Updated QSF", disabled=not (excel_file and qsf_file))

# ──────────────────────────────
# 🛠️ HELPER FUNCTIONS FROM UPDATED LOGIC
# ──────────────────────────────
def format_question_text(text):
    if ":" in text:
        bold, rest = text.split(":", 1)
        return f"<p><strong>{bold.strip()}:</strong> {rest.strip()}</p>"
    return f"<p>{text.strip()}</p>"

def get_qid_to_block_name_map(qsf_data):
    qid_to_block = {}
    for el in qsf_data["SurveyElements"]:
        if el.get("Element") == "BL":
            payload = el.get("Payload", {})
            if isinstance(payload, dict):
                block_name = payload.get("Description", "")
                for be in payload.get("BlockElements", []):
                    if isinstance(be, dict) and be.get("Type") == "Question":
                        qid_to_block[be["QuestionID"]] = block_name
    return qid_to_block

def restore_block_titles(qsf_data):
    qid_to_block = get_qid_to_block_name_map(qsf_data)
    for el in qsf_data["SurveyElements"]:
        if el.get("Element") == "BL":
            payload = el.get("Payload", {})
            if isinstance(payload, dict):
                qids = [be.get("QuestionID") for be in payload.get("BlockElements", []) if be.get("Type") == "Question"]
                titles = {qid_to_block.get(qid, "") for qid in qids if qid in qid_to_block}
                titles.discard("")
                if titles:
                    payload["Description"] = " / ".join(sorted(titles))
    return qsf_data

# ──────────────────────────────
# 🧠 UPDATED QSF EDIT LOGIC
# ──────────────────────────────
def apply_edits(df, qsf_data):
    qsf = copy.deepcopy(qsf_data)
    grouped = df.groupby("QuestionID")
    updated_elements = []
    deleted_qids = set()

    readonly_qids = {
        "Q462", "Q463", "Q464", "Q465", "Q466", "Q467", "Q468", "Q469", "Q470", "Q471", "Q472", "Q473", "Q474",
        "Q475", "Q476", "Q477", "Q478", "Q479", "Q480", "Q481", "Q482", "Q483", "Q484", "Q485", "Q487", "Q488",
        "Q489", "Q492", "Q493", "Q494", "Q495", "Q496", "Q497", "Q498", "Q499", "Q500", "Q501", "Q502", "Q503",
        "Q504", "Q505", "Q506", "Q507", "Q508", "Q509", "Q510", "Q511", "Q512", "Q518"
    }

    for el in qsf["SurveyElements"]:
        if el.get("Element") != "SQ":
            updated_elements.append(el)
            continue

        qid = el["PrimaryAttribute"]
        if qid in readonly_qids:
            updated_elements.append(el)
            continue

        payload = el.get("Payload", {})

        if qid not in grouped.groups:
            updated_elements.append(el)
            continue

        group = grouped.get_group(qid)

        # Delete question if flagged
        question_row = group[group["ElementType"] == "QuestionText"]
        if not question_row.empty:
            if str(question_row["Display Question (Yes/No)"].iloc[0]).strip().lower() == "no":
                deleted_qids.add(qid)
                continue

        # Remove display logic if flagged
        if "Display Logic (Yes/No)" in group.columns:
            logic_flags = group["Display Logic (Yes/No)"].dropna().astype(str).str.strip().str.lower()
            if "no" in logic_flags.values:
                payload.pop("DisplayLogic", None)

        for _, row in group.iterrows():
            etype = row["ElementType"]
            edited = str(row.get("EditedText", "")).strip()
            original = str(row.get("OriginalText", "")).strip()
            display_flag = str(row.get("Display Question (Yes/No)", "")).strip().lower()
            label = str(row.get("Label", "")).strip()

            if etype == "QuestionText":
                if edited and edited != original:
                    payload["QuestionText"] = format_question_text(edited)

            elif etype.startswith("ChoiceText") and "Choices" in payload:
                try:
                    choice_id = etype.split(" - ")[1].strip()
                    if display_flag == "no":
                        payload["Choices"].pop(choice_id, None)
                    elif choice_id in payload["Choices"]:
                        content = "Don't know" if not edited and not original else edited if edited else original
                        if choice_id in ("1", "2", "3") and label.lower() not in ("", "nan"):
                            formatted = f"<strong>{label}</strong><br>{content}"
                        else:
                            formatted = content
                        payload["Choices"][choice_id]["Display"] = formatted
                except Exception:
                    pass

        updated_elements.append(el)

    # Clean up deleted QIDs from blocks
    for el in updated_elements:
        if el.get("Element") == "BL":
            payload = el.get("Payload", {})
            if isinstance(payload, dict):
                payload["BlockElements"] = [
                    be for be in payload.get("BlockElements", [])
                    if be.get("Type") != "Question" or be.get("QuestionID") not in deleted_qids
                ]

    qsf_data["SurveyElements"] = updated_elements
    return restore_block_titles(qsf_data), deleted_qids

# ──────────────────────────────
# 🚀 PROCESS + DOWNLOAD
# ──────────────────────────────
if process_btn:
    try:
        df = pd.read_excel(excel_file, sheet_name="Survey_Edits")  # 🔄 updated sheet name here
        qsf_raw = json.load(qsf_file)
        updated_qsf, deleted_qids = apply_edits(df, qsf_raw)

        json_bytes = json.dumps(updated_qsf, indent=2, ensure_ascii=False).encode("utf-8")
        memfile = io.BytesIO(json_bytes)

        outname = f"Updated_Survey_{datetime.now():%Y%m%d_%H%M%S}.qsf"

        st.success("✅ QSF file successfully updated!")
        if deleted_qids:
            st.info("🗑️ Questions Deleted: " + ", ".join(sorted(deleted_qids)))

        st.download_button(
            "⬇️ Download Updated QSF",
            data=memfile,
            file_name=outname,
            mime="application/json",
        )

    except Exception as e:
        st.error(f"❌ Error: {e}")



