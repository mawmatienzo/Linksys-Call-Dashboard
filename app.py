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
    df['lob']      = df['lob'].fillna('Unknown')
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
    xaxis=dict(gridcolor='#2d3148', linecolor='#2d3148', tickcolor='#2d3148'),
    yaxis=dict(gridcolor='#2d3148', linecolor='#2d3148', tickcolor='#2d3148'),
    margin=dict(l=10, r=10, t=30, b=10),
    legend=dict(bgcolor='#1a1d27', bordercolor='#2d3148'),
    hoverlabel=dict(bgcolor='#222637', bordercolor='#2d3148'),
)

# ── MAIN ─────────────────────────────────────────────────────────────────────
st.sidebar.markdown("## 📞 Call Centre Dashboard")
st.sidebar.markdown("---")

uploaded = st.sidebar.file_uploader("⬆ Upload Excel File", type=['xlsx','xls'])

if uploaded:
    raw = load_data(uploaded.read())
else:
    st.info("👈 Upload your **Phone_Data.xlsx** file in the sidebar to get started.")
    st.stop()

# ── SIDEBAR FILTERS ───────────────────────────────────────────────────────────
st.sidebar.markdown("### 🔍 Filters")

# LOB
lobs = sorted(raw['lob'].unique())
sel_lobs = st.sidebar.multiselect("LOB", lobs, default=lobs)

# Queue (filtered by LOB)
df_lob = raw[raw['lob'].isin(sel_lobs)] if sel_lobs else raw
queues = sorted(df_lob['queue'].unique())
sel_queues = st.sidebar.multiselect("Queue", queues, default=queues)

# Warranty
warranties = sorted([w for w in raw['warranty'].unique() if w])
sel_warranty = st.sidebar.multiselect("Warranty", warranties, default=warranties)

st.sidebar.markdown("---")
st.sidebar.markdown("### 📅 Period")

# Month multi-select
all_months = sorted(raw['date'].dt.to_period('M').unique().astype(str))
month_labels = {m: pd.Period(m).strftime('%B %Y') for m in all_months}
sel_months = st.sidebar.multiselect(
    "Month", all_months,
    format_func=lambda m: month_labels[m],
    default=[]
)

# Week multi-select
all_weeks = sorted(raw['date'].apply(week_ending_saturday).dt.normalize().unique())
week_labels = {str(w.date()): 'WE ' + pd.Timestamp(w).strftime('%b %d, %Y') for w in all_weeks}
sel_weeks = st.sidebar.multiselect(
    "Week Ending", list(week_labels.keys()),
    format_func=lambda w: week_labels[w],
    default=[]
)

st.sidebar.markdown("---")
gran = st.sidebar.selectbox("📊 Group By", ["Daily","Weekly","Monthly"], index=2)

# ── APPLY FILTERS ─────────────────────────────────────────────────────────────
df = raw.copy()

# Skill filters
if sel_lobs:    df = df[df['lob'].isin(sel_lobs)]
if sel_queues:  df = df[df['queue'].isin(sel_queues)]
if sel_warranty:
    df = df[df['warranty'].isin(sel_warranty) | (df['warranty'] == '')]

# Period filters
if sel_weeks:
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
fig_vol.add_bar(x=agg['label'], y=agg['offered'],  name='Offered',   marker_color='rgba(79,142,247,.6)')
fig_vol.add_bar(x=agg['label'], y=agg['answered'], name='Answered',  marker_color='rgba(34,211,160,.6)')
fig_vol.add_bar(x=agg['label'], y=agg['abandon'],  name='Abandoned', marker_color='rgba(247,86,74,.6)')
fig_vol.update_layout(**PLOT_LAYOUT, title='Call Volume', barmode='group', height=300)
st.plotly_chart(fig_vol, use_container_width=True)

col1, col2 = st.columns(2)

# Abandon %
fig_abn = go.Figure()
fig_abn.add_scatter(x=agg['label'], y=agg['abn_pct'].round(1), mode='lines+markers',
    line=dict(color='#f7564a', width=2), marker=dict(size=4),
    fill='tozeroy', fillcolor='rgba(247,86,74,.08)')
fig_abn.update_layout(**PLOT_LAYOUT, title='Abandon Rate %', height=280,
    yaxis=dict(**PLOT_LAYOUT['yaxis'], ticksuffix='%'))
col1.plotly_chart(fig_abn, use_container_width=True)

# AHT
fig_aht = go.Figure()
fig_aht.add_scatter(x=agg['label'], y=agg['aht_sec'].round(0), mode='lines+markers',
    line=dict(color='#7c5cfc', width=2), marker=dict(size=4),
    fill='tozeroy', fillcolor='rgba(124,92,252,.08)')
fig_aht.update_layout(**PLOT_LAYOUT, title='Avg Handle Time (seconds)', height=280,
    yaxis=dict(**PLOT_LAYOUT['yaxis'], tickformat='.0f'))
col1.plotly_chart(fig_aht, use_container_width=True)

# SL %
fig_sl = go.Figure()
fig_sl.add_scatter(x=agg['label'], y=agg['cum_30'].round(1),  name='≤30s',  mode='lines', line=dict(color='#22d3a0', width=2))
fig_sl.add_scatter(x=agg['label'], y=agg['cum_60'].round(1),  name='≤60s',  mode='lines', line=dict(color='#22d3ee', width=2))
fig_sl.add_scatter(x=agg['label'], y=agg['cum_90'].round(1),  name='≤90s',  mode='lines', line=dict(color='#4f8ef7', width=2))
fig_sl.add_scatter(x=agg['label'], y=agg['cum_120'].round(1), name='≤120s', mode='lines', line=dict(color='#a78bfa', width=2))
fig_sl.update_layout(**PLOT_LAYOUT, title='Service Level %', height=280,
    yaxis=dict(**PLOT_LAYOUT['yaxis'], ticksuffix='%'))
col2.plotly_chart(fig_sl, use_container_width=True)

# ASA
fig_asa = go.Figure()
fig_asa.add_bar(x=agg['label'], y=agg['asa'].round(0), marker_color='rgba(245,166,35,.6)',
    marker_line=dict(color='#f5a623', width=1))
fig_asa.update_layout(**PLOT_LAYOUT, title='Avg Speed of Answer (seconds)', height=280)
col2.plotly_chart(fig_asa, use_container_width=True)

# ── DETAIL TABLE ──────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("### 📋 Detailed Data")

table = agg[['label','offered','answered','abandon','abn_pct','aht_sec','asa','cum_30','cum_60','cum_90','cum_120']].copy()
table.columns = ['Period','Offered','Answered','Abandoned','Abn %','AHT (s)','ASA (s)','≤30s %','≤60s %','≤90s %','≤120s %']
table['Abn %']   = table['Abn %'].round(1)
table['AHT (s)'] = table['AHT (s)'].round(0).astype(int)
table['ASA (s)'] = table['ASA (s)'].round(0).astype(int)
for c in ['≤30s %','≤60s %','≤90s %','≤120s %']:
    table[c] = table[c].round(1)

st.dataframe(table, use_container_width=True, hide_index=True,
    column_config={
        'Offered':   st.column_config.NumberColumn(format="%d"),
        'Answered':  st.column_config.NumberColumn(format="%d"),
        'Abandoned': st.column_config.NumberColumn(format="%d"),
        'Abn %':     st.column_config.NumberColumn(format="%.1f%%"),
        '≤30s %':    st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
        '≤60s %':    st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
        '≤90s %':    st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
        '≤120s %':   st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
    })

st.sidebar.markdown("---")
st.sidebar.caption(f"📁 {uploaded.name}  |  {len(df):,} rows loaded")
