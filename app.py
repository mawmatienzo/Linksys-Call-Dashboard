import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from io import BytesIO
import numpy as np

st.set_page_config(
    page_title="Call Centre Dashboard",
    page_icon="📞",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ── THEME ────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  [data-testid="stSidebar"] { background: #1a1d27; }
  .metric-card {
    background: #1a1d27; border: 1px solid #2d3148; border-radius: 12px;
    padding: 16px 18px; margin-bottom: 8px;
  }
  .metric-label { font-size: 0.72rem; color: #7b82a0; text-transform: uppercase;
    letter-spacing: .06em; font-weight: 600; margin-bottom: 4px; }
  .metric-value { font-size: 1.7rem; font-weight: 700; letter-spacing: -.03em; }
  .metric-sub   { font-size: 0.72rem; color: #7b82a0; margin-top: 4px; }
  .badge-good { background: rgba(34,211,160,.15); color: #22d3a0; padding: 2px 8px;
    border-radius: 20px; font-size: .7rem; font-weight: 700; }
  .badge-warn { background: rgba(245,166,35,.15); color: #f5a623; padding: 2px 8px;
    border-radius: 20px; font-size: .7rem; font-weight: 700; }
  .badge-bad  { background: rgba(247,86,74,.15);  color: #f7564a; padding: 2px 8px;
    border-radius: 20px; font-size: .7rem; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

MONTHS = ['January','February','March','April','May','June',
          'July','August','September','October','November','December']

# ── HELPERS ──────────────────────────────────────────────────────────────────
def fmt_mmss(seconds):
    if pd.isna(seconds) or seconds == 0: return "00:00"
    s = int(seconds)
    return f"{s//60:02d}:{s%60:02d}"

def load_chat_data(file_bytes):
    df = pd.read_excel(BytesIO(file_bytes), sheet_name='Sheet1')
    df = df[['Department(s)', 'Date', 'Total chat duration (secs)', 'Missed?']].copy()
    df.columns = ['department', 'date_raw', 'duration', 'missed']
    # Clean date — ISO format string like 2024-09-14T23:13:16-07:00
    df['date'] = pd.to_datetime(df['date_raw'].astype(str).str[:10], errors='coerce')
    df['interval'] = pd.to_datetime(df['date_raw'].astype(str).str[11:16], format='%H:%M', errors='coerce').dt.strftime('%H:%M')
    # Round interval down to nearest 30 min
    df['hour']   = pd.to_datetime(df['date_raw'].astype(str).str[11:16], format='%H:%M', errors='coerce').dt.hour
    df['minute'] = pd.to_datetime(df['date_raw'].astype(str).str[11:16], format='%H:%M', errors='coerce').dt.minute
    df['minute'] = (df['minute'] // 30) * 30
    df['interval'] = df['hour'].astype(str).str.zfill(2) + ':' + df['minute'].astype(str).str.zfill(2)
    df['duration'] = pd.to_numeric(df['duration'], errors='coerce').fillna(0)
    df['missed']   = df['missed'].astype(str).str.strip()
    df['offered']  = 1
    df['answered'] = (df['missed'] == 'No').astype(int)
    df['missed_n'] = (df['missed'] == 'Yes').astype(int)
    df = df.dropna(subset=['date'])
    return df

def week_ending_saturday(dt):
    """Ceiling to nearest Saturday."""
    day = dt.weekday()          # Mon=0 … Sun=6
    js_day = (day + 1) % 7     # convert to JS-style 0=Sun…6=Sat
    diff = 0 if js_day == 6 else 6 - js_day
    return dt + pd.Timedelta(days=diff)

def eomonth(dt):
    """End of month date."""
    return dt + pd.offsets.MonthEnd(0)

# ── LOAD DATA ────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_data(file_bytes):
    xl = pd.ExcelFile(BytesIO(file_bytes))

    # Sheet 1 — call data
    df = xl.parse('Sheet1')
    df.columns = df.columns.str.strip()
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.rename(columns={
        'Split/Queue':   'queue',
        'Interval':      'interval',
        'Calls Offered': 'offered',
        'Calls Answered':'answered',
        'Talktime':      'talktime',
        'Answer Time':   'answer_time',
        'ACW Time':      'acw',
        'Calls Abandon': 'abandon',
        'Ans < 30':      'ans_lt30',
        'Ans w/in 30':   'ans_30',
        'Ans w/in 60':   'ans_60',
        'Ans w/in 90':   'ans_90',
        'Ans w/in 120':  'ans_120',
    })
    num_cols = ['offered','answered','talktime','answer_time','acw',
                'abandon','ans_lt30','ans_30','ans_60','ans_90','ans_120']
    # Clean interval — keep as string HH:MM
    if 'interval' in df.columns:
        df['interval'] = df['interval'].astype(str).str[:5]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    df['queue'] = df['queue'].astype(int)
    df['date']  = df['Date'].dt.normalize()

    # Sheet 2 — queue mapping
    df2 = xl.parse('Sheet2')
    df2.columns = df2.columns.str.strip()
    # Drop header row if duplicated
    df2 = df2[df2.iloc[:,0] != df2.columns[0]]
    df2 = df2.rename(columns={
        df2.columns[0]: 'queue',
        df2.columns[1]: 'lob',
        df2.columns[2]: 'warranty'
    })
    df2['queue'] = pd.to_numeric(df2['queue'], errors='coerce').dropna().astype(int)
    df2['warranty'] = df2['warranty'].fillna('')

    df = df.merge(df2[['queue','lob','warranty']], on='queue', how='left')
    df['lob']      = df['lob'].fillna('NA')
    df['warranty'] = df['warranty'].fillna('')
    return df

# ── AGGREGATE ────────────────────────────────────────────────────────────────
def aggregate(df, gran):
    if gran == 'Daily':
        df = df.copy(); df['period'] = df['date']
        df['label'] = df['date'].dt.strftime('%Y-%m-%d')
    elif gran == 'Weekly':
        df = df.copy()
        df['period'] = df['date'].apply(week_ending_saturday).dt.normalize()
        df['label']  = 'WE ' + df['period'].dt.strftime('%b %d, %Y')
    else:  # Monthly
        df = df.copy()
        df['period'] = df['date'].apply(eomonth).dt.normalize()
        df['label']  = df['period'].dt.strftime('%B %Y')

    grp = df.groupby(['period','label'], sort=True).agg(
        offered    =('offered',   'sum'),
        answered   =('answered',  'sum'),
        abandon    =('abandon',   'sum'),
        talktime   =('talktime',  'sum'),
        acw        =('acw',       'sum'),
        answer_time=('answer_time','sum'),
        ans_lt30   =('ans_lt30',  'sum'),
        ans_30     =('ans_30',    'sum'),
        ans_60     =('ans_60',    'sum'),
        ans_90     =('ans_90',    'sum'),
        ans_120    =('ans_120',   'sum'),
    ).reset_index()

    grp['aht_sec'] = np.where(grp['answered']>0, (grp['talktime']+grp['acw'])/grp['answered'], 0)
    grp['asa']     = np.where(grp['answered']>0, grp['answer_time']/grp['answered'], 0)
    grp['abn_pct'] = np.where(grp['offered']>0,  grp['abandon']/grp['offered']*100, 0)
    grp['cum_120'] = np.where(grp['answered']>0,
        (grp['ans_lt30']+grp['ans_30']+grp['ans_60']+grp['ans_90']+grp['ans_120'])/grp['answered']*100, 0)
    grp['cum_30']  = np.where(grp['answered']>0, (grp['ans_lt30']+grp['ans_30'])/grp['answered']*100, 0)
    grp['cum_60']  = np.where(grp['answered']>0, (grp['ans_lt30']+grp['ans_30']+grp['ans_60'])/grp['answered']*100, 0)
    grp['cum_90']  = np.where(grp['answered']>0, (grp['ans_lt30']+grp['ans_30']+grp['ans_60']+grp['ans_90'])/grp['answered']*100, 0)
    return grp

# ── PLOTLY THEME ─────────────────────────────────────────────────────────────
PLOT_LAYOUT = dict(
    paper_bgcolor='#1a1d27', plot_bgcolor='#1a1d27',
    font=dict(color='#e8eaf0', family='Segoe UI, system-ui, sans-serif', size=11),
    margin=dict(l=10, r=10, t=30, b=10),
    legend=dict(bgcolor='#1a1d27', bordercolor='#2d3148'),
    hoverlabel=dict(bgcolor='#222637', bordercolor='#2d3148'),
)
XAXIS_BASE = dict(gridcolor='#2d3148', linecolor='#2d3148', tickcolor='#2d3148')
YAXIS_BASE = dict(gridcolor='#2d3148', linecolor='#2d3148', tickcolor='#2d3148')
XAXIS_INT  = dict(gridcolor='#2d3148', linecolor='#2d3148', tickcolor='#2d3148', tickangle=45)

# ── MAIN ─────────────────────────────────────────────────────────────────────
st.sidebar.markdown("## 📞 Call Centre Dashboard")
st.sidebar.markdown("---")

import os, urllib.request

# Try to load data from the repo file, fallback to upload
DATA_FILE = 'Phone_Data.xlsx'

@st.cache_data(show_spinner="Loading data...")
def load_from_file(path, mtime):
    with open(path, 'rb') as f:
        return load_data(f.read())

CHAT_FILE = 'Chat_Data.xlsx'

@st.cache_data(show_spinner="Loading chat data...")
def load_chat_file(path, mtime):
    with open(path, 'rb') as f:
        return load_chat_data(f.read())

if os.path.exists(DATA_FILE):
    raw = load_from_file(DATA_FILE, os.path.getmtime(DATA_FILE))
else:
    uploaded = st.sidebar.file_uploader("⬆ Upload Excel File", type=['xlsx','xls'])
    if uploaded:
        raw = load_data(uploaded.read())
    else:
        st.info("👈 Upload your **Phone_Data.xlsx** file in the sidebar to get started.")
        st.stop()

if os.path.exists(CHAT_FILE):
    chat_raw = load_chat_file(CHAT_FILE, os.path.getmtime(CHAT_FILE))
else:
    chat_raw = None

# ── SIDEBAR FILTERS ───────────────────────────────────────────────────────────
st.sidebar.markdown("### 🔍 Filters")

# LOB — phone + CHAT combined
lobs = sorted(raw['lob'].unique())
all_lobs = lobs + (['CHAT'] if chat_raw is not None else [])
sel_lobs = st.sidebar.multiselect("LOB", all_lobs, default=all_lobs)
# Separate phone vs chat selections
sel_phone_lobs = [l for l in sel_lobs if l != 'CHAT']
sel_chat_selected = 'CHAT' in sel_lobs

# Queue (filtered by LOB)
df_lob = raw[raw['lob'].isin(sel_lobs)] if sel_lobs else raw
queues = sorted(df_lob['queue'].unique())
sel_queues = st.sidebar.multiselect("Queue", queues, default=queues)

# Warranty
warranties = sorted([w for w in raw['warranty'].unique() if w])
sel_warranty = st.sidebar.multiselect("Warranty", warranties, default=warranties)

st.sidebar.markdown("---")
st.sidebar.markdown("### 📅 Period")

# Date multi-select
all_dates_raw = sorted(raw['date'].dt.date.unique(), reverse=True)
sel_dates = st.sidebar.multiselect(
    "Date", all_dates_raw,
    format_func=lambda d: d.strftime('%Y-%m-%d'),
    default=[]
)

# Week Ending multi-select
all_weeks = sorted(raw['date'].apply(week_ending_saturday).dt.normalize().unique(), reverse=True)
week_labels = {str(w.date()): 'WE ' + pd.Timestamp(w).strftime('%b %d, %Y') for w in all_weeks}
sel_weeks = st.sidebar.multiselect(
    "Week Ending", list(week_labels.keys()),
    format_func=lambda w: week_labels[w],
    default=[]
)

# Month multi-select
all_months = sorted(raw['date'].dt.to_period('M').unique().astype(str), reverse=True)
month_labels = {m: pd.Period(m).strftime('%B %Y') for m in all_months}
sel_months = st.sidebar.multiselect(
    "Month", all_months,
    format_func=lambda m: month_labels[m],
    default=[]
)

st.sidebar.markdown("---")
gran = st.sidebar.selectbox("📊 Group By", ["Daily","Weekly","Monthly"], index=2)

# ── APPLY FILTERS ─────────────────────────────────────────────────────────────
df = raw.copy()

# Skill filters
# Only apply phone filter if phone LOBs are selected
# If ONLY CHAT is selected, filter phone df to nothing
only_chat_selected = sel_chat_selected and len(sel_phone_lobs) == 0
only_phone_selected = not sel_chat_selected and len(sel_phone_lobs) > 0
show_phone = not only_chat_selected
show_chat  = sel_chat_selected

if sel_phone_lobs and show_phone:
    df = df[df['lob'].isin(sel_phone_lobs)]
if sel_queues:  df = df[df['queue'].isin(sel_queues)]
if sel_warranty:
    df = df[df['warranty'].isin(sel_warranty) | (df['warranty'] == '')]

# Period filters — Date takes priority, then Week, then Month
if sel_dates:
    df = df[df['date'].dt.date.isin(sel_dates)]
elif sel_weeks:
    week_dates = pd.to_datetime(sel_weeks)
    df = df[df['date'].apply(week_ending_saturday).dt.normalize().isin(week_dates)]
elif sel_months:
    df = df[df['date'].dt.to_period('M').astype(str).isin(sel_months)]

if df.empty:
    st.warning("No data matches the selected filters.")
    st.stop()

agg = aggregate(df, gran)

# ── KPI CARDS ─────────────────────────────────────────────────────────────────
tot_off = int(df['offered'].sum())
tot_ans = int(df['answered'].sum())
tot_abn = int(df['abandon'].sum())
ans_rate = tot_ans / tot_off * 100 if tot_off > 0 else 0
avg_aht  = (df['talktime'].sum() + df['acw'].sum()) / max(tot_ans, 1)
avg_abn  = tot_abn / tot_off * 100 if tot_off > 0 else 0
avg_sl   = (df['ans_lt30']+df['ans_30']+df['ans_60']+df['ans_90']+df['ans_120']).sum() / max(tot_ans,1) * 100
avg_asa  = df['answer_time'].sum() / max(tot_ans, 1)

def kpi(col, label, value, sub, color, badge=None):
    badge_html = f'<div class="{badge[1]}">{badge[0]}</div>' if badge else ''
    col.markdown(f"""
<div class="metric-card" style="border-top: 3px solid {color}">
  <div class="metric-label">{label}</div>
  <div class="metric-value" style="color:{color}">{value}</div>
  <div class="metric-sub">{sub}</div>
  {badge_html}
</div>""", unsafe_allow_html=True)

if show_phone:
    st.markdown("### 📊 Summary")
    c1,c2,c3,c4,c5,c6,c7 = st.columns(7)
    
    def kpi(col, label, value, sub, color, badge=None):
        badge_html = f'<div class="{badge[1]}">{badge[0]}</div>' if badge else ''
        col.markdown(f"""
        <div class="metric-card" style="border-top: 3px solid {color}">
          <div class="metric-label">{label}</div>
          <div class="metric-value" style="color:{color}">{value}</div>
          <div class="metric-sub">{sub}</div>
          {badge_html}
        </div>""", unsafe_allow_html=True)
    
    kpi(c1, "Calls Offered",    f"{tot_off:,}",          "Total inbound",          "#4f8ef7")
    kpi(c2, "Calls Answered",   f"{tot_ans:,}",           f"{ans_rate:.1f}% answer rate", "#22d3a0")
    kpi(c3, "Calls Abandoned",  f"{tot_abn:,}",           "Did not reach agent",    "#f7564a")
    kpi(c4, "Abandon Rate",     f"{avg_abn:.1f}%",        "of offered calls",       "#f7564a" if avg_abn>15 else "#f5a623" if avg_abn>10 else "#22d3a0",
        ("High","badge-bad") if avg_abn>15 else ("Watch","badge-warn") if avg_abn>10 else ("OK","badge-good"))
    kpi(c5, "Avg Handle Time",  fmt_mmss(avg_aht),        "(Talk + ACW) ÷ Calls",   "#7c5cfc")
    kpi(c6, "SL ≤120s",         f"{avg_sl:.1f}%",         "% answered within 120s", "#22d3a0" if avg_sl>=80 else "#f5a623" if avg_sl>=50 else "#f7564a",
        ("Target Met","badge-good") if avg_sl>=80 else ("Near Target","badge-warn") if avg_sl>=50 else ("Below Target","badge-bad"))
    kpi(c7, "Avg Speed of Answer", fmt_mmss(avg_asa),     "Ring to pickup",         "#22d3a0" if avg_asa<=30 else "#f5a623" if avg_asa<=60 else "#f7564a")
    
    st.markdown("---")
    
    # ── CHARTS ────────────────────────────────────────────────────────────────────
    # Volume chart
    fig_vol = go.Figure()
    fig_vol.add_scatter(x=agg['label'], y=agg['offered'],  name='Offered',
        mode='lines+markers', line=dict(color='#4f8ef7', width=2), marker=dict(size=4))
    fig_vol.add_scatter(x=agg['label'], y=agg['answered'], name='Answered',
        mode='lines+markers', line=dict(color='#22d3a0', width=2), marker=dict(size=4))
    fig_vol.add_scatter(x=agg['label'], y=agg['abandon'],  name='Abandoned',
        mode='lines+markers', line=dict(color='#f7564a', width=2), marker=dict(size=4))
    fig_vol.update_layout(**PLOT_LAYOUT, title='Call Volume', height=300, xaxis=XAXIS_BASE, yaxis=YAXIS_BASE)
    st.plotly_chart(fig_vol, use_container_width=True)
    
    col1, col2 = st.columns(2)
    
    # Abandon %
    fig_abn = go.Figure()
    fig_abn.add_scatter(x=agg['label'], y=agg['abn_pct'].round(1), mode='lines+markers',
        line=dict(color='#f7564a', width=2), marker=dict(size=4),
        fill='tozeroy', fillcolor='rgba(247,86,74,.08)')
    fig_abn.update_layout(**PLOT_LAYOUT, title='Abandon Rate %', height=280, xaxis=XAXIS_BASE, yaxis=dict(**YAXIS_BASE, ticksuffix='%'))
    col1.plotly_chart(fig_abn, use_container_width=True)
    
    # AHT
    fig_aht = go.Figure()
    aht_vals = agg['aht_sec'].round(0)
    aht_text = aht_vals.apply(lambda s: f"{int(s)//60:02d}:{int(s)%60:02d}")
    fig_aht.add_scatter(x=agg['label'], y=aht_vals, mode='lines+markers',
        line=dict(color='#7c5cfc', width=2), marker=dict(size=4),
        fill='tozeroy', fillcolor='rgba(124,92,252,.08)',
        text=aht_text, hovertemplate='%{x}<br>AHT: %{text}<extra></extra>')
    # Every 5 minutes (300 seconds)
    aht_min = int(aht_vals.min())
    aht_max = int(aht_vals.max())
    start_tick = (aht_min // 300) * 300
    tick_vals = list(range(start_tick, aht_max + 300, 300))
    tick_text = [f"{v//60:02d}:00" for v in tick_vals]
    fig_aht.update_layout(**PLOT_LAYOUT, title='Avg Handle Time (MM:SS)', height=280,
        yaxis=dict(**YAXIS_BASE, tickvals=tick_vals, ticktext=tick_text,
                   range=[start_tick - 60, aht_max + 120]))
    col1.plotly_chart(fig_aht, use_container_width=True)
    
    # SL %
    fig_sl = go.Figure()
    fig_sl.add_scatter(x=agg['label'], y=agg['cum_120'].round(1), name='≤120s',
        mode='lines+markers', line=dict(color='#a78bfa', width=2), marker=dict(size=4),
        fill='tozeroy', fillcolor='rgba(167,139,250,.08)')
    fig_sl.update_layout(**PLOT_LAYOUT, title='Service Level %', height=280, xaxis=XAXIS_BASE, yaxis=dict(**YAXIS_BASE, ticksuffix='%'))
    col2.plotly_chart(fig_sl, use_container_width=True)
    
    # ASA
    fig_asa = go.Figure()
    asa_vals = agg['asa'].round(0)
    asa_text = asa_vals.apply(lambda s: f"{int(s)//60:02d}:{int(s)%60:02d}")
    fig_asa.add_bar(x=agg['label'], y=asa_vals, marker_color='rgba(245,166,35,.6)',
        marker_line=dict(color='#f5a623', width=1),
        text=asa_text, hovertemplate='%{x}<br>ASA: %{text}<extra></extra>')
    asa_min = int(asa_vals.min() * 0.97)
    asa_max = int(asa_vals.max() * 1.03)
    tick_vals_a = [int(asa_min + i*(asa_max-asa_min)/4) for i in range(5)]
    tick_text_a = [f"{v//60:02d}:{v%60:02d}" for v in tick_vals_a]
    fig_asa.update_layout(**PLOT_LAYOUT, title='Avg Speed of Answer (MM:SS)', height=280,
        yaxis=dict(**YAXIS_BASE, tickvals=tick_vals_a, ticktext=tick_text_a, range=[asa_min, asa_max]))
    col2.plotly_chart(fig_asa, use_container_width=True)
    
    # ── DETAIL TABLE ──────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📋 Detailed Data")
    
    table = agg[['label','offered','answered','abandon','abn_pct','aht_sec','asa','cum_120']].copy()
    table.columns = ['Period','Offered','Answered','Abandoned','Abn %','AHT','ASA','SL ≤120s %']
    table['Abn %']      = table['Abn %'].round(1)
    table['AHT']        = table['AHT'].round(0).apply(lambda s: f"{int(s)//60:02d}:{int(s)%60:02d}")
    table['ASA']        = table['ASA'].round(0).apply(lambda s: f"{int(s)//60:02d}:{int(s)%60:02d}")
    table['SL ≤120s %'] = table['SL ≤120s %'].round(1)
    
    st.dataframe(table, use_container_width=True, hide_index=True,
        column_config={
            'Offered':    st.column_config.NumberColumn(format="%d"),
            'Answered':   st.column_config.NumberColumn(format="%d"),
            'Abandoned':  st.column_config.NumberColumn(format="%d"),
            'Abn %':      st.column_config.NumberColumn(format="%.1f%%"),
            'AHT':        st.column_config.TextColumn(),
            'ASA':        st.column_config.TextColumn(),
            'SL ≤120s %': st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
        })
    
    # ── PER INTERVAL TABLE ───────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### ⏱ Per Interval Data")
    
    # Use the already-filtered df (sidebar filters already applied)
    df_int = df.copy()
    
    # Aggregate by interval
    if 'interval' in df_int.columns:
        grp_int = df_int.groupby('interval', sort=True).agg(
            offered    =('offered',    'sum'),
            answered   =('answered',   'sum'),
            abandon    =('abandon',    'sum'),
            talktime   =('talktime',   'sum'),
            acw        =('acw',        'sum'),
            answer_time=('answer_time','sum'),
            ans_lt30   =('ans_lt30',   'sum'),
            ans_30     =('ans_30',     'sum'),
            ans_60     =('ans_60',     'sum'),
            ans_90     =('ans_90',     'sum'),
            ans_120    =('ans_120',    'sum'),
        ).reset_index()
    
        grp_int['aht_sec'] = np.where(grp_int['answered']>0, (grp_int['talktime']+grp_int['acw'])/grp_int['answered'], 0)
        grp_int['asa']     = np.where(grp_int['answered']>0, grp_int['answer_time']/grp_int['answered'], 0)
        grp_int['abn_pct'] = np.where(grp_int['offered']>0,  grp_int['abandon']/grp_int['offered']*100, 0)
        grp_int['sl_120']  = np.where(grp_int['answered']>0,
            (grp_int['ans_lt30']+grp_int['ans_30']+grp_int['ans_60']+grp_int['ans_90']+grp_int['ans_120'])/grp_int['answered']*100, 0)
    
        tbl_int = grp_int[['interval','offered','answered','abandon','abn_pct','aht_sec','asa','sl_120']].copy()
        tbl_int.columns = ['Interval','Offered','Answered','Abandoned','Abn %','AHT','ASA','SL ≤120s %']
        tbl_int['Abn %']      = tbl_int['Abn %'].round(1)
        tbl_int['AHT']        = tbl_int['AHT'].round(0).apply(lambda s: f"{int(s)//60:02d}:{int(s)%60:02d}")
        tbl_int['ASA']        = tbl_int['ASA'].round(0).apply(lambda s: f"{int(s)//60:02d}:{int(s)%60:02d}")
        tbl_int['SL ≤120s %'] = tbl_int['SL ≤120s %'].round(1)
    
        st.dataframe(tbl_int, use_container_width=True, hide_index=True,
            column_config={
                'Interval':   st.column_config.TextColumn(),
                'Offered':    st.column_config.NumberColumn(format="%d"),
                'Answered':   st.column_config.NumberColumn(format="%d"),
                'Abandoned':  st.column_config.NumberColumn(format="%d"),
                'Abn %':      st.column_config.NumberColumn(format="%.1f%%"),
                'AHT':        st.column_config.TextColumn(),
                'ASA':        st.column_config.TextColumn(),
                'SL ≤120s %': st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
            })
        # ── Per Interval Line Charts ─────────────────────────────────────────────
        st.markdown("#### Call Volume by Interval")
        fig_int_vol = go.Figure()
        fig_int_vol.add_scatter(x=grp_int['interval'], y=grp_int['offered'],  name='Offered',
            mode='lines+markers', line=dict(color='#4f8ef7', width=2), marker=dict(size=4))
        fig_int_vol.add_scatter(x=grp_int['interval'], y=grp_int['answered'], name='Answered',
            mode='lines+markers', line=dict(color='#22d3a0', width=2), marker=dict(size=4))
        fig_int_vol.add_scatter(x=grp_int['interval'], y=grp_int['abandon'],  name='Abandoned',
            mode='lines+markers', line=dict(color='#f7564a', width=2), marker=dict(size=4))
        fig_int_vol.update_layout(**PLOT_LAYOUT, height=280, yaxis=YAXIS_BASE,
            xaxis=XAXIS_INT)
        st.plotly_chart(fig_int_vol, use_container_width=True)
    
        col_ig1, col_ig2, col_ig3 = st.columns(3)
    
        # Abandon %
        fig_int_abn = go.Figure()
        fig_int_abn.add_scatter(x=grp_int['interval'], y=grp_int['abn_pct'].round(1),
            mode='lines+markers', line=dict(color='#f7564a', width=2), marker=dict(size=3),
            fill='tozeroy', fillcolor='rgba(247,86,74,.08)')
        fig_int_abn.update_layout(**PLOT_LAYOUT, title='Abandon Rate %', height=220,
            yaxis=dict(**YAXIS_BASE, ticksuffix='%'),
            xaxis=XAXIS_INT)
        col_ig1.plotly_chart(fig_int_abn, use_container_width=True)
    
        # AHT
        fig_int_aht = go.Figure()
        aht_i = grp_int['aht_sec'].round(0)
        aht_i_min = int(aht_i.min()); aht_i_max = int(aht_i.max())
        start_i = (aht_i_min // 300) * 300
        tv_i = list(range(start_i, aht_i_max + 300, 300))
        tt_i = [f"{v//60:02d}:00" for v in tv_i]
        aht_i_text = aht_i.apply(lambda s: f"{int(s)//60:02d}:{int(s)%60:02d}")
        fig_int_aht.add_scatter(x=grp_int['interval'], y=aht_i,
            mode='lines+markers', line=dict(color='#7c5cfc', width=2), marker=dict(size=3),
            text=aht_i_text, hovertemplate='%{x}<br>AHT: %{text}<extra></extra>',
            fill='tozeroy', fillcolor='rgba(124,92,252,.08)')
        fig_int_aht.update_layout(**PLOT_LAYOUT, title='Avg Handle Time (MM:SS)', height=220,
            yaxis=dict(**YAXIS_BASE, tickvals=tv_i, ticktext=tt_i, range=[start_i-60, aht_i_max+120]),
            xaxis=XAXIS_INT)
        col_ig2.plotly_chart(fig_int_aht, use_container_width=True)
    
        # ASA
        fig_int_asa = go.Figure()
        asa_i = grp_int['asa'].round(0)
        asa_i_min = int(asa_i.min()); asa_i_max = int(asa_i.max())
        start_ia = (asa_i_min // 300) * 300
        tv_ia = list(range(start_ia, asa_i_max + 300, 300))
        tt_ia = [f"{v//60:02d}:00" for v in tv_ia]
        asa_i_text = asa_i.apply(lambda s: f"{int(s)//60:02d}:{int(s)%60:02d}")
        fig_int_asa.add_scatter(x=grp_int['interval'], y=asa_i,
            mode='lines+markers', line=dict(color='#f5a623', width=2), marker=dict(size=3),
            text=asa_i_text, hovertemplate='%{x}<br>ASA: %{text}<extra></extra>',
            fill='tozeroy', fillcolor='rgba(245,166,35,.08)')
        fig_int_asa.update_layout(**PLOT_LAYOUT, title='Avg Speed of Answer (MM:SS)', height=220,
            yaxis=dict(**YAXIS_BASE, tickvals=tv_ia, ticktext=tt_ia, range=[start_ia-60, asa_i_max+120]),
            xaxis=XAXIS_INT)
        col_ig3.plotly_chart(fig_int_asa, use_container_width=True)
    
        # SL
        fig_int_sl = go.Figure()
        fig_int_sl.add_scatter(x=grp_int['interval'], y=grp_int['sl_120'].round(1),
            mode='lines+markers', line=dict(color='#a78bfa', width=2), marker=dict(size=3),
            fill='tozeroy', fillcolor='rgba(167,139,250,.08)')
        fig_int_sl.update_layout(**PLOT_LAYOUT, title='Service Level ≤120s %', height=220,
            yaxis=dict(**YAXIS_BASE, ticksuffix='%'),
            xaxis=XAXIS_INT)
        col_ig1.plotly_chart(fig_int_sl, use_container_width=True)
    
    else:
        st.info("Interval column not found in uploaded data.")
    
    # ── CHAT SECTION ─────────────────────────────────────────────────────────────
    
st.markdown("---")
st.markdown("## 💬 Level 1 Chat")

if chat_raw is not None and show_chat:
    df_chat = chat_raw.copy()
    if sel_dates:
        df_chat = df_chat[df_chat['date'].dt.date.isin(sel_dates)]
    elif sel_weeks:
        week_dates = pd.to_datetime(sel_weeks)
        df_chat = df_chat[df_chat['date'].apply(week_ending_saturday).dt.normalize().isin(week_dates)]
    elif sel_months:
        df_chat = df_chat[df_chat['date'].dt.to_period('M').astype(str).isin(sel_months)]

    if df_chat.empty:
        st.warning("No chat data matches the selected filters.")
    else:
        # ── Chat KPI cards ───────────────────────────────────────────────────
        c_offered  = int(df_chat['offered'].sum())
        c_answered = int(df_chat['answered'].sum())
        c_missed   = int(df_chat['missed_n'].sum())
        c_abn_pct  = round(c_missed / c_offered * 100, 1) if c_offered > 0 else 0
        c_duration = df_chat.loc[df_chat['answered']==1, 'duration'].sum()
        c_cht_sec  = round(c_duration / c_answered, 1) if c_answered > 0 else 0
        c_cht_mmss = f"{int(c_cht_sec)//60:02d}:{int(c_cht_sec)%60:02d}"

        cc1,cc2,cc3,cc4,cc5 = st.columns(5)
        kpi(cc1, "Offered Chat",   f"{c_offered:,}",    "Total chats offered",       "#4f8ef7")
        kpi(cc2, "Answered Chat",  f"{c_answered:,}",   f"{round(c_answered/c_offered*100,1) if c_offered else 0}% answer rate", "#22d3a0")
        kpi(cc3, "Missed Chat",    f"{c_missed:,}",     "Not answered",              "#f7564a")
        kpi(cc4, "Abandoned %",    f"{c_abn_pct}%",     "Missed / Offered",
            "#f7564a" if c_abn_pct>15 else "#f5a623" if c_abn_pct>10 else "#22d3a0",
            ("High","badge-bad") if c_abn_pct>15 else ("Watch","badge-warn") if c_abn_pct>10 else ("OK","badge-good"))
        kpi(cc5, "Chat Handle Time", c_cht_mmss,        "Duration ÷ Answered",       "#7c5cfc")

        st.markdown("---")

        # ── Aggregate by period for charts ───────────────────────────────────
        if gran == 'Daily':
            df_chat['period'] = df_chat['date']
            df_chat['label']  = df_chat['date'].dt.strftime('%Y-%m-%d')
        elif gran == 'Weekly':
            df_chat['period'] = df_chat['date'].apply(week_ending_saturday).dt.normalize()
            df_chat['label']  = 'WE ' + df_chat['period'].dt.strftime('%b %d, %Y')
        else:
            df_chat['period'] = df_chat['date'].apply(eomonth).dt.normalize()
            df_chat['label']  = df_chat['period'].dt.strftime('%B %Y')

        grp_c = df_chat.groupby(['period','label'], sort=True).agg(
            offered =('offered',  'sum'),
            answered=('answered', 'sum'),
            missed  =('missed_n', 'sum'),
            duration=('duration', 'sum'),
        ).reset_index()
        grp_c['abn_pct'] = np.where(grp_c['offered']>0, grp_c['missed']/grp_c['offered']*100, 0)
        grp_c['cht_sec'] = np.where(grp_c['answered']>0, grp_c['duration']/grp_c['answered'], 0)

        # Chat Volume line chart
        fig_cv = go.Figure()
        fig_cv.add_scatter(x=grp_c['label'], y=grp_c['offered'],  name='Offered',
            mode='lines+markers', line=dict(color='#4f8ef7', width=2), marker=dict(size=4))
        fig_cv.add_scatter(x=grp_c['label'], y=grp_c['answered'], name='Answered',
            mode='lines+markers', line=dict(color='#22d3a0', width=2), marker=dict(size=4))
        fig_cv.add_scatter(x=grp_c['label'], y=grp_c['missed'],   name='Missed',
            mode='lines+markers', line=dict(color='#f7564a', width=2), marker=dict(size=4))
        fig_cv.update_layout(**PLOT_LAYOUT, title='Chat Volume', height=280,
            xaxis=XAXIS_BASE, yaxis=YAXIS_BASE)
        st.plotly_chart(fig_cv, use_container_width=True)

        col_c1, col_c2 = st.columns(2)

        # Abandoned %
        fig_ca = go.Figure()
        fig_ca.add_scatter(x=grp_c['label'], y=grp_c['abn_pct'].round(1),
            mode='lines+markers', line=dict(color='#f7564a', width=2), marker=dict(size=4),
            fill='tozeroy', fillcolor='rgba(247,86,74,.08)')
        fig_ca.update_layout(**PLOT_LAYOUT, title='Abandoned Chat %', height=250,
            xaxis=XAXIS_BASE, yaxis=dict(**YAXIS_BASE, ticksuffix='%'))
        col_c1.plotly_chart(fig_ca, use_container_width=True)

        # Chat Handle Time
        fig_ch = go.Figure()
        cht_vals = grp_c['cht_sec'].round(0)
        cht_text = cht_vals.apply(lambda s: f"{int(s)//60:02d}:{int(s)%60:02d}")
        cht_min = int(cht_vals.min()); cht_max = int(cht_vals.max())
        start_c = (cht_min // 300) * 300
        tv_c  = list(range(start_c, cht_max + 300, 300))
        tt_c  = [f"{v//60:02d}:00" for v in tv_c]
        fig_ch.add_scatter(x=grp_c['label'], y=cht_vals,
            mode='lines+markers', line=dict(color='#7c5cfc', width=2), marker=dict(size=4),
            text=cht_text, hovertemplate='%{x}<br>CHT: %{text}<extra></extra>',
            fill='tozeroy', fillcolor='rgba(124,92,252,.08)')
        fig_ch.update_layout(**PLOT_LAYOUT, title='Chat Handle Time (MM:SS)', height=250,
            xaxis=XAXIS_BASE, yaxis=dict(**YAXIS_BASE, tickvals=tv_c, ticktext=tt_c,
            range=[start_c - 60, cht_max + 120]))
        col_c2.plotly_chart(fig_ch, use_container_width=True)

        # ── Per Interval Chat Table & Charts ─────────────────────────────────
        st.markdown("---")
        st.markdown("### ⏱ Chat Per Interval Data")

        grp_ci = df_chat.groupby('interval', sort=True).agg(
            offered =('offered',  'sum'),
            answered=('answered', 'sum'),
            missed  =('missed_n', 'sum'),
            duration=('duration', 'sum'),
        ).reset_index()
        grp_ci['abn_pct'] = np.where(grp_ci['offered']>0, grp_ci['missed']/grp_ci['offered']*100, 0)
        grp_ci['cht_sec'] = np.where(grp_ci['answered']>0, grp_ci['duration']/grp_ci['answered'], 0)

        # Interval volume chart
        fig_civ = go.Figure()
        fig_civ.add_scatter(x=grp_ci['interval'], y=grp_ci['offered'],  name='Offered',
            mode='lines+markers', line=dict(color='#4f8ef7', width=2), marker=dict(size=3))
        fig_civ.add_scatter(x=grp_ci['interval'], y=grp_ci['answered'], name='Answered',
            mode='lines+markers', line=dict(color='#22d3a0', width=2), marker=dict(size=3))
        fig_civ.add_scatter(x=grp_ci['interval'], y=grp_ci['missed'],   name='Missed',
            mode='lines+markers', line=dict(color='#f7564a', width=2), marker=dict(size=3))
        fig_civ.update_layout(**PLOT_LAYOUT, title='Chat Volume by Interval', height=250,
            xaxis=XAXIS_INT, yaxis=YAXIS_BASE)
        st.plotly_chart(fig_civ, use_container_width=True)

        col_ci1, col_ci2 = st.columns(2)

        fig_cia = go.Figure()
        fig_cia.add_scatter(x=grp_ci['interval'], y=grp_ci['abn_pct'].round(1),
            mode='lines+markers', line=dict(color='#f7564a', width=2), marker=dict(size=3),
            fill='tozeroy', fillcolor='rgba(247,86,74,.08)')
        fig_cia.update_layout(**PLOT_LAYOUT, title='Abandoned % by Interval', height=220,
            xaxis=XAXIS_INT, yaxis=dict(**YAXIS_BASE, ticksuffix='%'))
        col_ci1.plotly_chart(fig_cia, use_container_width=True)

        fig_cih = go.Figure()
        chti_vals = grp_ci['cht_sec'].round(0)
        chti_text = chti_vals.apply(lambda s: f"{int(s)//60:02d}:{int(s)%60:02d}")
        chti_min = int(chti_vals.min()); chti_max = int(chti_vals.max())
        start_ci = (chti_min // 300) * 300
        tv_ci = list(range(start_ci, chti_max + 300, 300))
        tt_ci = [f"{v//60:02d}:00" for v in tv_ci]
        fig_cih.add_scatter(x=grp_ci['interval'], y=chti_vals,
            mode='lines+markers', line=dict(color='#7c5cfc', width=2), marker=dict(size=3),
            text=chti_text, hovertemplate='%{x}<br>CHT: %{text}<extra></extra>',
            fill='tozeroy', fillcolor='rgba(124,92,252,.08)')
        fig_cih.update_layout(**PLOT_LAYOUT, title='Chat Handle Time by Interval (MM:SS)', height=220,
            xaxis=XAXIS_INT, yaxis=dict(**YAXIS_BASE, tickvals=tv_ci, ticktext=tt_ci,
            range=[start_ci - 60, chti_max + 120]))
        col_ci2.plotly_chart(fig_cih, use_container_width=True)

        # Interval table
        tbl_ci = grp_ci[['interval','offered','answered','missed','abn_pct','cht_sec']].copy()
        tbl_ci.columns = ['Interval','Offered','Answered','Missed','Abandoned %','CHT']
        tbl_ci['Abandoned %'] = tbl_ci['Abandoned %'].round(1)
        tbl_ci['CHT'] = tbl_ci['CHT'].round(0).apply(lambda s: f"{int(s)//60:02d}:{int(s)%60:02d}")
        st.dataframe(tbl_ci, use_container_width=True, hide_index=True,
            column_config={
                'Offered':     st.column_config.NumberColumn(format="%d"),
                'Answered':    st.column_config.NumberColumn(format="%d"),
                'Missed':      st.column_config.NumberColumn(format="%d"),
                'Abandoned %': st.column_config.NumberColumn(format="%.1f%%"),
                'CHT':         st.column_config.TextColumn(),
            })
elif chat_raw is None:
    st.info("Chat_Data.xlsx not found in repository.")
elif not show_chat:
    st.info("Select CHAT in the LOB filter to view chat data.")

st.sidebar.markdown("---")
st.sidebar.caption(f"📊 {len(df):,} rows loaded")
