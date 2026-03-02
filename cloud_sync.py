import os
import hashlib
import json
import pandas as pd
import streamlit as st
from streamlit_gsheets import GSheetsConnection

SHEET_URL = "https://docs.google.com/spreadsheets/d/1tZWB0MvrJeSCFPtY829AwDz7iL-nJm4uDUJUv9UVoJs/edit?gid=494566932#gid=494566932"

def get_conn():
    return st.connection("gsheets", type=GSheetsConnection)

def get_dir_hash(directory="data"):
    """Creates a unique fingerprint of all your local data files."""
    if not os.path.exists(directory): return ""
    hasher = hashlib.md5()
    for root, dirs, files in os.walk(directory):
        for names in sorted(files):
            filepath = os.path.join(root, names)
            if not names.startswith('.'):
                try:
                    with open(filepath, 'rb') as f: hasher.update(f.read())
                except: pass
    return hasher.hexdigest()

# TEMPORARILY DISABLED CACHE FOR DEBUGGING
# @st.cache_data(ttl=3600, show_spinner=False)
def pull_from_cloud():
    """Downloads cloud data to local disk on first boot."""
    try:
        conn = get_conn()
        os.makedirs("data", exist_ok=True)
        
        # Pull CSVs
        for tab in ["Holdings", "Cashflow", "Trades"]:
            try:
                df = conn.read(spreadsheet=SHEET_URL, worksheet=tab)
                if not df.empty and len(df.columns) > 1:
                    df.to_csv(f"data/{tab.lower()}.csv", index=False)
            except Exception as e: 
                print(f"🚨 FAILED to pull {tab} CSV: {e}")

        # Pull JSONs (Stored securely as text strings)
        for tab in ["Profile", "Settings"]:
            try:
                df = conn.read(spreadsheet=SHEET_URL, worksheet=tab)
                if not df.empty and len(df) > 0:
                    json_str = str(df.iloc[0, 0])
                    if json_str.strip().startswith("{"):
                        data_dict = json.loads(json_str)
                        for file_name, content in data_dict.items():
                            with open(f"data/{file_name}", "w") as f:
                                json.dump(content, f, indent=4)
            except Exception as e: 
                print(f"🚨 FAILED to pull {tab} JSON: {e}")
    except Exception as e: 
        print(f"🚨 FAILED main cloud pull connection: {e}")

def push_to_cloud():
    """Uploads all local data to Google Sheets."""
    try:
        conn = get_conn()
        if not os.path.exists("data"): return

        for tab in ["Holdings", "Cashflow", "Trades"]:
            filename = f"data/{tab.lower()}.csv"
            if os.path.exists(filename):
                try:
                    conn.update(spreadsheet=SHEET_URL, worksheet=tab, data=pd.read_csv(filename))
                except Exception as e: 
                    print(f"🚨 FAILED to push {tab} CSV: {e}")

        profile_data = {}
        settings_data = {}
        for file_name in os.listdir("data"):
            if file_name.endswith(".json"):
                try:
                    with open(f"data/{file_name}", "r") as f: content = json.load(f)
                    if "tax_profile" in file_name: profile_data[file_name] = content
                    else: settings_data[file_name] = content
                except Exception: pass
        
        if profile_data:
            try:
                conn.update(spreadsheet=SHEET_URL, worksheet="Profile", data=pd.DataFrame({"json_data": [json.dumps(profile_data)]}))
            except Exception as e:
                print(f"🚨 FAILED to push Profile JSON: {e}")
        if settings_data:
            try:
                conn.update(spreadsheet=SHEET_URL, worksheet="Settings", data=pd.DataFrame({"json_data": [json.dumps(settings_data)]}))
            except Exception as e:
                print(f"🚨 FAILED to push Settings JSON: {e}")
            
    except Exception as e: print(f"Cloud Push Error: {e}")

def run_auto_sync():
    """Runs continuously in app.py to silently sync saves."""
    if "cloud_synced" not in st.session_state:
        pull_from_cloud()
        st.session_state["cloud_synced"] = True
        st.session_state["last_hash"] = get_dir_hash()
        return

    current_hash = get_dir_hash()
    if "last_hash" in st.session_state and st.session_state["last_hash"] != current_hash:
        with st.spinner("☁️ Backing up changes to cloud database..."):
            push_to_cloud()
        st.session_state["last_hash"] = current_hash
