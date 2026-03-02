import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd

st.title("☁️ Cloud Database Test")

# Put your URL right here in the code
SHEET_URL = "https://docs.google.com/spreadsheets/d/1tZWB0MvrJeSCFPtY829AwDz7iL-nJm4uDUJUv9UVoJs/edit?gid=494566932#gid=494566932"

try:
    # 1. Connect to Google Sheets and explicitly pass the URL
    conn = st.connection("gsheets", type=GSheetsConnection)
    
    # 2. Create some test data
    test_data = pd.DataFrame({
        "Status": ["Connection Successful!"],
        "Message": ["Your Streamlit app can now read and write to the cloud."]
    })
    
    # 3. Write it to the "Settings" tab in your Google Sheet
    conn.update(spreadsheet=SHEET_URL, worksheet="Settings", data=test_data)
    
    # 4. Read it back to prove it worked
    df = conn.read(spreadsheet=SHEET_URL, worksheet="Settings", usecols=[0, 1])
    
    st.success("✅ Connected to Google Sheets!")
    st.write("Here is the data we just successfully wrote to and read from the cloud:")
    st.dataframe(df)
    
except Exception as e:
    st.error("🚨 Connection Failed.")
    st.write(f"Error details: {e}")
