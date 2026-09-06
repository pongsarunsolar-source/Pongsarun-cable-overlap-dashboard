import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
import math
import io
import requests
import time
from streamlit_geolocation import streamlit_geolocation

# ==========================================
# 1. ตั้งค่าหน้าจอ
# ==========================================
st.set_page_config(page_title="Cable Replacement Dashboard", layout="wide")

# ==========================================
# 2. ฟังก์ชันช่วยเหลือ (Helper Functions)
# ==========================================
@st.cache_data(show_spinner=False)
def get_zone_from_location(lat, lon):
    try:
        geolocator = Nominatim(user_agent="cable_dashboard_app")
        location = geolocator.reverse(f"{lat}, {lon}", language='th', timeout=5)
        if location:
            address = location.address
            if 'ปากช่อง' in address: return 'PKG'
            if 'นครราชสีมา' in address: return 'NMA'
            if 'ชัยภูมิ' in address: return 'CPM'
        return 'Other'
    except:
        return 'Other'

def assign_zone(row):
    record_by = str(row.get('Record by', '')).strip().lower()
    if record_by == 'pongsark': return 'NMA'
    elif record_by == 'sompocn': return 'CPM'
    elif record_by == 'kuntholj': return 'PKG'
    else: return get_zone_from_location(row['Lat_Start'], row['Lon_Start'])

@st.cache_data(show_spinner=False)
def calculate_route_distance(route_coords):
    if len(route_coords) < 2: return 0
    dist = 0
    for i in range(len(route_coords)-1):
        dist += geodesic(route_coords[i], route_coords[i+1]).meters
    return dist

@st.cache_data(show_spinner=False)
def get_road_route(lat1, lon1, lat2, lon2):
    # ระยะกระจัด (เส้นตรงแบบขึงสาย Cable)
    direct_dist = geodesic((lat1, lon1), (lat2, lon2)).meters
    
    try:
        url = f"http://router.project-osrm.org/route/v1/foot/{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data['code'] == 'Ok':
                coords = data['routes'][0]['geometry']['coordinates']
                route = [[lat, lon] for lon, lat in coords]
                route_dist = calculate_route_distance(route)
                
                # 💡 กฎเหล็กตัด U-Turn ทั้งส้มและม่วง 100%: 
                # ถ้าระยะทาง OSRM พาวิ่งอ้อม หรือ U-turn ไกลกว่าเส้นตรงขึงตึง > 20% (และระยะต่างเกิน 20 ม.)
                # ให้ตัดทิ้ง แล้ว "วาดเส้นตรง (เหมือนเส้นแดงที่คุณวาด)" แทนทันที
                if route_dist > direct_dist * 1.20 and (route_dist - direct_dist) > 20:
                    return [[lat1, lon1], [lat2, lon2]]
                
                return route
    except:
        pass
    
    # กรณี API ล่ม ให้ขึงเส้นตรง
    return [[lat1, lon1], [lat2, lon2]]

def offset_route(route_coords, offset_deg=0.0004):
    if len(route_coords) < 2:
        return route_coords
    
    start, end = route_coords[0], route_coords[-1]
    dy = end[0] - start[0]
    dx = end[1] - start[1]
    if dx == 0 and dy == 0:
        return route_coords
        
    angle = math.atan2(dy, dx)
    perp_angle = angle + (math.pi / 2) 
    
    off_lat = offset_deg * math.sin(perp_angle)
    off_lon = offset_deg * math.cos(perp_angle)
    
    return [[lat + off_lat, lon + off_lon] for lat, lon in route_coords]

def get_midpoint(lat1, lon1, lat2, lon2):
    return (lat1 + lat2) / 2, (lon1 + lon2) / 2

def check_overlap(new_start, new_stop, base_df):
    if base_df.empty: return []
    new_mid = get_midpoint(new_start[0], new_start[1], new_stop[0], new_stop[1])
    results = []
    
    for idx, row in base_df.iterrows():
        base_mid = get_midpoint(row['Lat_Start'], row['Lon_Start'], row['Lat_Stop'], row['Lon_Stop'])
        dist = geodesic(new_mid, base_mid).meters
        
        if dist <= 500:
            tracker_id = str(row.get('Tracker_ID', row.get('Tracker II', row.get('Tracker_II', '-'))))
            
            results.append({
                'Base_Tracker_ID': tracker_id,
                'Base_Site_Code': row.get('Site_Code', '-'),
                'Base_Site_B': row.get('Site_B', '-'),
                'Base_Cable_Type': row.get('Cable Type', '-'),
                'Base_Status': row.get('Status', '-'),
                'Distance_Diff_Meters': round(dist, 2),
                'Remark': f"อยู่ในระยะ Improvement {tracker_id} + ห่างจากเดิม {round(dist, 2)} เมตร",
                'Base_Lat_Start': row['Lat_Start'], 'Base_Lon_Start': row['Lon_Start'],
                'Base_Lat_Stop': row['Lat_Stop'], 'Base_Lon_Stop': row['Lon_Stop'],
                'Check_Lat_Start': new_start[0], 'Check_Lon_Start': new_start[1],
                'Check_Lat_Stop': new_stop[0], 'Check_Lon_Stop': new_stop[1]
            })
    return results

def clean_coords(df):
    cols = ['Lat_Start', 'Lon_Start', 'Lat_Stop', 'Lon_Stop']
    for col in cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace('\u200b', '', regex=False).str.replace('-', '', regex=False).str.strip()
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.dropna(subset=['Lat_Start', 'Lon_Start', 'Lat_Stop', 'Lon_Stop'])

# --- ฟังก์ชันช่วยเหลือสำหรับ AI Suggestion ---
@st.cache_data(show_spinner=False)
def fetch_road_name(lat, lon):
    try:
        geolocator = Nominatim(user_agent="cable_merge_app")
        loc = geolocator.reverse(f"{lat}, {lon}", timeout=5, language='th')
        time.sleep(0.5) 
        if loc and 'address' in loc.raw:
            addr = loc.raw['address']
            return addr.get('road', addr.get('highway', addr.get('street', 'ไม่ทราบชื่อถนน')))
        return 'ไม่ทราบชื่อถนน'
    except:
        return 'ไม่ทราบชื่อถนน'

def get_min_distance_between_jobs(job1, job2):
    pts1 = [(job1['Lat_Start'], job1['Lon_Start']), (job1['Lat_Stop'], job1['Lon_Stop'])]
    pts2 = [(job2['Lat_Start'], job2['Lon_Start']), (job2['Lat_Stop'], job2['Lon_Stop'])]
    min_d = float('inf')
    for p1 in pts1:
        for p2 in pts2:
            d = geodesic(p1, p2).meters
            if d < min_d:
                min_d = d
    return min_d

# 💡 ลอจิกใหม่: หาจุดไกลสุด 2 จุด แล้วร้อยตะเข็บทีละงานเพื่อคลุมตัว L ให้มิด!
def create_merged_plan(cluster_jobs, rd_name):
    merged_name = " + ".join([str(x.get('Name', f"Job")) for x in cluster_jobs])
    n = len(cluster_jobs)
    
    if n == 1:
        job = cluster_jobs[0]
        route = get_road_route(job['Lat_Start'], job['Lon_Start'], job['Lat_Stop'], job['Lon_Stop'])
        dist = calculate_route_distance(route)
        return {
            'Name': job.get('Name', 'Job'),
            'Road_Name': rd_name,
            'Lat_Start': job['Lat_Start'],
            'Lon_Start': job['Lon_Start'],
            'Lat_Stop': job['Lat_Stop'],
            'Lon_Stop': job['Lon_Stop'],
            'Merged_Count': 1,
            'Estimated_Cable_Meters': round(dist, 2),
            'Original_Jobs': cluster_jobs,
            'Full_Route': route
        }

    # 1. เทพิกัดทุกหมุดรวมกัน หาขั้ว 2 ฝั่งที่ไกลกันที่สุด
    endpoints = []
    for i, job in enumerate(cluster_jobs):
        endpoints.append({'job_idx': i, 'pt': (job['Lat_Start'], job['Lon_Start']), 'type': 'start'})
        endpoints.append({'job_idx': i, 'pt': (job['Lat_Stop'], job['Lon_Stop']), 'type': 'stop'})
        
    max_dist = -1
    ext_p1 = endpoints[0]
    ext_p2 = endpoints[0]
    
    for i in range(len(endpoints)):
        for j in range(i+1, len(endpoints)):
            d = geodesic(endpoints[i]['pt'], endpoints[j]['pt']).meters
            if d > max_dist:
                max_dist = d
                ext_p1 = endpoints[i]
                ext_p2 = endpoints[j]

    # 2. เริ่มเย็บตะเข็บร้อยจุด (Stitching) จากจุดที่ไกลที่สุดที่ 1 (ext_p1)
    full_route = []
    visited_jobs = set()
    
    curr_pt = ext_p1['pt']
    curr_job_idx = ext_p1['job_idx']
    
    job = cluster_jobs[curr_job_idx]
    other_pt = (job['Lat_Stop'], job['Lon_Stop']) if ext_p1['type'] == 'start' else (job['Lat_Start'], job['Lon_Start'])
    
    # ลากเส้นผ่านงานแรก
    job_rt = get_road_route(curr_pt[0], curr_pt[1], other_pt[0], other_pt[1])
    full_route.extend(job_rt)
    
    visited_jobs.add(curr_job_idx)
    curr_pt = other_pt
    ordered_jobs = [job]
    
    # 3. วนลูปหางานที่ใกล้ที่สุดถัดไปเรื่อยๆ จนครบทุกเส้น
    while len(visited_jobs) < n:
        min_dist = float('inf')
        best_job_idx = None
        best_enter_pt = None
        best_exit_pt = None
        
        for i, jb in enumerate(cluster_jobs):
            if i not in visited_jobs:
                p1 = (jb['Lat_Start'], jb['Lon_Start'])
                p2 = (jb['Lat_Stop'], jb['Lon_Stop'])
                
                # หาด้านที่หันหัวเข้าหาเราใกล้ที่สุด
                d1 = geodesic(curr_pt, p1).meters
                d2 = geodesic(curr_pt, p2).meters
                
                if d1 < min_dist:
                    min_dist = d1
                    best_job_idx = i
                    best_enter_pt = p1
                    best_exit_pt = p2
                if d2 < min_dist:
                    min_dist = d2
                    best_job_idx = i
                    best_enter_pt = p2
                    best_exit_pt = p1
                    
        # ลากเส้นสะพานเชื่อม
        bridge_rt = get_road_route(curr_pt[0], curr_pt[1], best_enter_pt[0], best_enter_pt[1])
        if full_route and bridge_rt:
            full_route.extend(bridge_rt[1:]) 
        else:
            full_route.extend(bridge_rt)
            
        # ลากเส้นผ่านงานถัดไป
        job_rt = get_road_route(best_enter_pt[0], best_enter_pt[1], best_exit_pt[0], best_exit_pt[1])
        if full_route and job_rt:
            full_route.extend(job_rt[1:])
        else:
            full_route.extend(job_rt)
            
        visited_jobs.add(best_job_idx)
        ordered_jobs.append(cluster_jobs[best_job_idx])
        curr_pt = best_exit_pt

    # 4. คำนวณระยะทางทั้งหมดรวดเดียว
    total_cable = calculate_route_distance(full_route)
    
    return {
        'Name': f"[Merged] {merged_name}",
        'Road_Name': rd_name,
        'Lat_Start': ext_p1['pt'][0],
        'Lon_Start': ext_p1['pt'][1],
        'Lat_Stop': curr_pt[0], # จุดสุดท้ายของการเดิน (ซึ่งคือ ext_p2)
        'Lon_Stop': curr_pt[1],
        'Merged_Count': n,
        'Estimated_Cable_Meters': round(total_cable, 2),
        'Original_Jobs': ordered_jobs,
        'Full_Route': full_route
    }

# ==========================================
# 3. ตั้งค่า Session State
# ==========================================
if 'view_mode' not in st.session_state:
    st.session_state.view_mode = 'dashboard' 
if 'overlap_results_df' not in st.session_state:
    st.session_state.overlap_results_df = None
if 'overlap_lines_to_draw' not in st.session_state:
    st.session_state.overlap_lines_to_draw = []
if 'active_large_map' not in st.session_state:
    st.session_state.active_large_map = 'OVERALL'
if 'prev_clicks' not in st.session_state:
    st.session_state.prev_clicks = {'nma': None, 'cpm': None, 'pkg': None, 'large': None}
if 'current_filter' not in st.session_state:
    st.session_state.current_filter = None

if 'm_start_input' not in st.session_state:
    st.session_state.m_start_input = ""
if 'm_stop_input' not in st.session_state:
    st.session_state.m_stop_input = ""
    
if 'is_ai_mode' not in st.session_state:
    st.session_state.is_ai_mode = False
if 'ai_results_df' not in st.session_state:
    st.session_state.ai_results_df = None
if 'ai_display_df' not in st.session_state:
    st.session_state.ai_display_df = None

# ==========================================
# 4. ส่วน Sidebar
# ==========================================
st.sidebar.title("การจัดการข้อมูล")
st.sidebar.markdown("**Import ภาพรวมข้อมูล**")
uploaded_main_file = st.sidebar.file_uploader("อัปโหลดไฟล์ Excel (Main)", type=["xlsx"], key="main_upload")

main_df = pd.DataFrame()
if uploaded_main_file:
    main_df = pd.read_excel(uploaded_main_file)
    main_df = clean_coords(main_df)
    
    if 'Distance(M)' not in main_df.columns:
        main_df['Distance(M)'] = 0.0
    main_df['Distance(M)'] = pd.to_numeric(main_df['Distance(M)'], errors='coerce').fillna(0)
    
    main_df['Status_Clean'] = main_df.get('Status', '').astype(str).str.strip().str.lower()
    main_df['Cable_Type_Clean'] = main_df.get('Cable Type', '').astype(str).str.strip().str.lower()
    main_df['Map_Tooltip'] = main_df['Site_Code'].astype(str) + " -> " + main_df['Site_B'].astype(str) + " (" + main_df['Cable Type'].astype(str) + ") | ระยะทาง: " + main_df['Distance(M)'].astype(str) + " ม."

st.sidebar.divider()
st.sidebar.markdown("### Check Route Overlap")

with st.sidebar.expander("Manual Check", expanded=True):
    st.caption("รูปแบบ: Lat, Long (เช่น 15.507, 102.330)")
    
    st.markdown("**📍 ดึงพิกัดมือถือ (GPS)**")
    gps_target = st.radio(
        "เลือกช่องที่ต้องการนำพิกัดไปใส่:",
        ["จุดเริ่มต้น (Start)", "จุดสิ้นสุด (Stop)"]
    )
    
    loc = streamlit_geolocation()
    if loc and loc.get('latitude') is not None:
        new_gps = f"{loc['latitude']}, {loc['longitude']}"
        
        if gps_target == "จุดเริ่มต้น (Start)":
            if st.session_state.m_start_input != new_gps:
                st.session_state.m_start_input = new_gps
                st.rerun()
        elif gps_target == "จุดสิ้นสุด (Stop)":
            if st.session_state.m_stop_input != new_gps:
                st.session_state.m_stop_input = new_gps
                st.rerun()
            
    st.markdown("<hr style='margin: 10px 0;'>", unsafe_allow_html=True)
    
    st.markdown("**จุดเริ่มต้น (Start)**")
    m_start_val = st.text_input(
        "Lat/Long Start", 
        placeholder="Lat, Lon", 
        key="m_start_input",
        label_visibility="collapsed"
    )
    
    st.markdown("**จุดสิ้นสุด (Stop)**")
    m_stop_val = st.text_input(
        "Lat/Long Stop", 
        placeholder="Lat, Lon", 
        key="m_stop_input",
        label_visibility="collapsed"
    )
    
    st.markdown("<hr style='margin: 10px 0;'>", unsafe_allow_html=True)
    
    if st.button("Check Overlap", type="primary", use_container_width=True):
        if m_start_val and m_stop_val:
            try:
                s_parts = m_start_val.split(',')
                e_parts = m_stop_val.split(',')
                new_start = (float(s_parts[0].strip()), float(s_parts[1].strip()))
                new_stop = (float(e_parts[0].strip()), float(e_parts[1].strip()))
                
                overlaps = check_overlap(new_start, new_stop, main_df) if not main_df.empty else []
                st.session_state.overlap_lines_to_draw = [{'name': 'Manual Line', 'start': new_start, 'stop': new_stop}]
                
                if overlaps:
                    res_df = pd.DataFrame(overlaps)
                    res_df.insert(0, 'Checked_Name', 'Manual Line')
                    st.session_state.overlap_results_df = res_df
                else:
                    st.session_state.overlap_results_df = pd.DataFrame()
                
                st.session_state.view_mode = 'overlap'
                st.session_state.is_ai_mode = False
            except Exception:
                st.sidebar.error("❌ รูปแบบพิกัดไม่ถูกต้อง กรุณากรอกแบบ 'Lat, Long' และคั่นด้วยลูกน้ำ")
        else:
            st.sidebar.warning("กรุณากรอกพิกัดให้ครบทั้ง 2 ช่อง")

st.sidebar.markdown("**Import Check route Overlap (Excel)**")
uploaded_overlap_files = st.sidebar.file_uploader("อัปโหลดไฟล์ Excel (Overlap)", type=["xlsx"], key="overlap_upload", accept_multiple_files=True)

if uploaded_overlap_files:
    overlap_dfs = []
    for file in uploaded_overlap_files:
        df_temp = pd.read_excel(file)
        overlap_dfs.append(df_temp)
    
    overlap_df = pd.concat(overlap_dfs, ignore_index=True)
    overlap_df = clean_coords(overlap_df)
    
    if st.sidebar.button("Process Overlap File", type="primary", use_container_width=True):
        all_results = []
        lines_to_draw = []
        
        for idx, row in overlap_df.iterrows():
            new_start = (row['Lat_Start'], row['Lon_Start'])
            new_stop = (row['Lat_Stop'], row['Lon_Stop'])
            name = row.get('Name', f'Line_{idx}')
            
            lines_to_draw.append({'name': name, 'start': new_start, 'stop': new_stop})
            overlaps = check_overlap(new_start, new_stop, main_df) if not main_df.empty else []
            
            for ol in overlaps:
                ol['Checked_Name'] = name
                all_results.append(ol)
                
        st.session_state.overlap_lines_to_draw = lines_to_draw
        st.session_state.overlap_results_df = pd.DataFrame(all_results)
        st.session_state.view_mode = 'overlap'
        st.session_state.is_ai_mode = False

    st.sidebar.markdown("---")
    st.sidebar.markdown("**🤖 AI วิเคราะห์แผนคร่อมยาว**")
    
    if st.sidebar.button("✨ วิเคราะห์และยุบรวมแผน (AI Merge)", type="secondary", use_container_width=True):
        with st.spinner("กำลังให้ AI คำนวณ (ใช้กฎเหล็กห้าม U-turn)..."):
            roads = []
            for idx, row in overlap_df.iterrows():
                roads.append(fetch_road_name(row['Lat_Start'], row['Lon_Start']))
            overlap_df['Road_Name'] = roads
            
            merged_plans = []
            
            for road, group in overlap_df.groupby('Road_Name'):
                if road == 'ไม่ทราบชื่อถนน':
                    for _, row in group.iterrows():
                        merged_plans.append({
                            'Name': row.get('Name', 'Job'),
                            'Road_Name': road,
                            'Lat_Start': row['Lat_Start'],
                            'Lon_Start': row['Lon_Start'],
                            'Lat_Stop': row['Lat_Stop'],
                            'Lon_Stop': row['Lon_Stop'],
                            'Merged_Count': 1,
                            'Estimated_Cable_Meters': 0,
                            'Original_Jobs': [row.to_dict()],
                            'Full_Route': []
                        })
                    continue
                
                jobs = [row.to_dict() for _, row in group.iterrows()]
                visited = set()
                
                for i in range(len(jobs)):
                    if i in visited: continue
                    
                    cluster_indices = [i]
                    visited.add(i)
                    queue = [i]
                    
                    while queue:
                        curr = queue.pop(0)
                        for j in range(len(jobs)):
                            if j not in visited:
                                dist = get_min_distance_between_jobs(jobs[curr], jobs[j])
                                if dist <= 5000:
                                    visited.add(j)
                                    cluster_indices.append(j)
                                    queue.append(j)
                    
                    cluster_jobs = [jobs[idx] for idx in cluster_indices]
                    merged_plans.append(create_merged_plan(cluster_jobs, road))
            
            display_plans = []
            cluster_id = 1
            colors = ['#e6f2ff', '#e6ffe6', '#fff2e6', '#ffe6e6', '#f2e6ff', '#ffffe6'] 
            
            for plan in merged_plans:
                if plan['Merged_Count'] > 1:
                    bg_color = colors[cluster_id % len(colors)]
                    jobs = plan['Original_Jobs']
                    
                    for i, job in enumerate(jobs):
                        if i == 0:
                            remark = "จุดเริ่มต้นแผนคร่อมยาว"
                        elif i == len(jobs) - 1:
                            remark = "จุดสิ้นสุดแผนคร่อมยาว"
                        else:
                            remark = "จุดระหว่างทางแผนคร่อมยาว"
                            
                        display_plans.append({
                            'Road_Name': plan['Road_Name'],
                            'Name': job.get('Name', 'Job'),
                            'Remark': remark,
                            'Lat_Start': job['Lat_Start'],
                            'Lon_Start': job['Lon_Start'],
                            'Lat_Stop': job['Lat_Stop'],
                            'Lon_Stop': job['Lon_Stop'],
                            'BgColor': bg_color,
                            'Group_Total_Cable': plan['Estimated_Cable_Meters']
                        })
                    cluster_id += 1
                else:
                    job = plan['Original_Jobs'][0]
                    display_plans.append({
                        'Road_Name': plan['Road_Name'],
                        'Name': job.get('Name', 'Job'),
                        'Remark': 'งานเดี่ยว',
                        'Lat_Start': job['Lat_Start'],
                        'Lon_Start': job['Lon_Start'],
                        'Lat_Stop': job['Lat_Stop'],
                        'Lon_Stop': job['Lon_Stop'],
                        'BgColor': '',
                        'Group_Total_Cable': 0
                    })
                
            st.session_state.overlap_lines_to_draw = [] 
            st.session_state.ai_results_df = pd.DataFrame(merged_plans) 
            st.session_state.ai_display_df = pd.DataFrame(display_plans) 
            st.session_state.view_mode = 'overlap'
            st.session_state.is_ai_mode = True

# ==========================================
# 5. การแสดงผลหน้าจอหลัก (Main Content)
# ==========================================
st.title("Cable Replacement Dashboard")

if st.session_state.view_mode == 'dashboard':
    if main_df.empty:
        st.info("👈 กรุณาอัปโหลดไฟล์ Excel ภาพรวมข้อมูล (Main) เพื่อเปิดใช้ Dashboard หรืออัปโหลดไฟล์ Overlap ด้านล่างเพื่อวิเคราะห์ AI ทันที")
    else:
        use_3_col_layout = False
        
        if 'Zone' in main_df.columns:
            unique_raw_zones = [str(z).strip().upper() for z in main_df['Zone'].unique() if pd.notna(z) and str(z).strip() != '']
            
            if len(unique_raw_zones) == 1 and unique_raw_zones[0] == 'NMA':
                with st.spinner("กำลังตรวจสอบและกระจายพื้นที่ย่อย..."):
                    main_df['Calculated_Zone'] = main_df.apply(assign_zone, axis=1)
                use_3_col_layout = True
            else:
                main_df['Calculated_Zone'] = main_df['Zone'].astype(str).str.strip().str.upper()
                use_3_col_layout = False
        else:
            with st.spinner("กำลังตรวจสอบและกระจายพื้นที่ย่อย..."):
                main_df['Calculated_Zone'] = main_df.apply(assign_zone, axis=1)
            use_3_col_layout = True

        def create_map(df_zone):
            if df_zone.empty:
                return folium.Map(location=[15.8700, 101.5000], zoom_start=7)
            
            center_lat = df_zone['Lat_Start'].mean()
            center_lon = df_zone['Lon_Start'].mean()
            m = folium.Map(location=[center_lat, center_lon], zoom_start=10)
            
            for index, row in df_zone.iterrows():
                route_coords = get_road_route(row['Lat_Start'], row['Lon_Start'], row['Lat_Stop'], row['Lon_Stop'])
                
                cable_type = str(row.get('Cable Type', '')).strip().lower()
                if cable_type == '24c': line_color = "red"
                elif cable_type == '12c': line_color = "pink"
                elif cable_type == '96c': line_color = "green"
                else: line_color = "gray"
                
                folium.PolyLine(locations=route_coords, color=line_color, weight=4, opacity=0.7, tooltip=row['Map_Tooltip']).add_to(m)
            return m

        if use_3_col_layout:
            df_nma = main_df[main_df['Calculated_Zone'] == 'NMA']
            df_cpm = main_df[main_df['Calculated_Zone'] == 'CPM']
            df_pkg = main_df[main_df['Calculated_Zone'] == 'PKG']
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown("<h4 style='text-align: center;'>NMA</h4>", unsafe_allow_html=True)
                map_data_nma = st_folium(create_map(df_nma), width="100%", height=300, key="map_nma")
                st.caption(f"จำนวนงาน: {len(df_nma)} | ระยะทางรวม: {df_nma['Distance(M)'].sum():,.2f} ม.")
                if st.button("🔍 ขยาย NMA", use_container_width=True): st.session_state.active_large_map = 'NMA'
            
            with col2:
                st.markdown("<h4 style='text-align: center;'>CPM</h4>", unsafe_allow_html=True)
                map_data_cpm = st_folium(create_map(df_cpm), width="100%", height=300, key="map_cpm")
                st.caption(f"จำนวนงาน: {len(df_cpm)} | ระยะทางรวม: {df_cpm['Distance(M)'].sum():,.2f} ม.")
                if st.button("🔍 ขยาย CPM", use_container_width=True): st.session_state.active_large_map = 'CPM'

            with col3:
                st.markdown("<h4 style='text-align: center;'>PKG</h4>", unsafe_allow_html=True)
                map_data_pkg = st_folium(create_map(df_pkg), width="100%", height=300, key="map_pkg")
                st.caption(f"จำนวนงาน: {len(df_pkg)} | ระยะทางรวม: {df_pkg['Distance(M)'].sum():,.2f} ม.")
                if st.button("🔍 ขยาย PKG", use_container_width=True): st.session_state.active_large_map = 'PKG'

            st.markdown("---")
            if st.button("🗺️ ดูภาพรวมทั้งหมด (Overall Map & Summary)", use_container_width=True, type="primary"):
                st.session_state.active_large_map = 'OVERALL'

            df_large = df_nma if st.session_state.active_large_map == 'NMA' else df_cpm if st.session_state.active_large_map == 'CPM' else df_pkg if st.session_state.active_large_map == 'PKG' else main_df
            st.markdown(f"### 📊 สรุปรายละเอียด: {st.session_state.active_large_map}")
            
            comp_count = len(df_large[df_large['Status_Clean'] == 'completed'])
            prog_count = len(df_large[df_large['Status_Clean'] == 'in progress'])
            
            m_c1, m_c2, m_c3, m_c4, m_c5 = st.columns(5)
            m_c1.info(f"✅ **Completed:**\n\n{comp_count} งาน")
            m_c2.warning(f"⏳ **In Progress:**\n\n{prog_count} งาน")
            m_c3.success(f"**12C:**\n\n{df_large[df_large['Cable_Type_Clean'] == '12c']['Distance(M)'].sum():,.0f} ม.")
            m_c4.error(f"**24C:**\n\n{df_large[df_large['Cable_Type_Clean'] == '24c']['Distance(M)'].sum():,.0f} ม.")
            m_c5.success(f"**96C:**\n\n{df_large[df_large['Cable_Type_Clean'] == '96c']['Distance(M)'].sum():,.0f} ม.")

            map_data_large = st_folium(create_map(df_large), width="100%", height=500, key=f"large_map_{st.session_state.active_large_map}")

            curr_nma = map_data_nma.get("last_object_clicked_tooltip") if map_data_nma else None
            curr_cpm = map_data_cpm.get("last_object_clicked_tooltip") if map_data_cpm else None
            curr_pkg = map_data_pkg.get("last_object_clicked_tooltip") if map_data_pkg else None
            curr_large = map_data_large.get("last_object_clicked_tooltip") if map_data_large else None

            if curr_nma != st.session_state.prev_clicks['nma']:
                st.session_state.current_filter = curr_nma; st.session_state.prev_clicks['nma'] = curr_nma
            elif curr_cpm != st.session_state.prev_clicks['cpm']:
                st.session_state.current_filter = curr_cpm; st.session_state.prev_clicks['cpm'] = curr_cpm
            elif curr_pkg != st.session_state.prev_clicks['pkg']:
                st.session_state.current_filter = curr_pkg; st.session_state.prev_clicks['pkg'] = curr_pkg
            elif curr_large != st.session_state.prev_clicks['large']:
                st.session_state.current_filter = curr_large; st.session_state.prev_clicks['large'] = curr_large

            clicked_tooltip = st.session_state.current_filter
            st.markdown("---")
            
            if clicked_tooltip:
                st.subheader("🔍 รายละเอียดข้อมูลที่คุณเลือกจากแผนที่")
                df_display = df_large[df_large['Map_Tooltip'] == clicked_tooltip]
                if st.button("❌ ยกเลิกการเลือก"):
                    st.session_state.current_filter = None
                    st.rerun()
            else:
                st.subheader("📄 รายละเอียดข้อมูลทั้งหมด")
                df_display = df_large
                
            st.dataframe(df_display.drop(columns=['Map_Tooltip', 'Calculated_Zone', 'Status_Clean', 'Cable_Type_Clean'], errors='ignore'))

        else:
            st.markdown("### 🗺️ Dashboard แผนที่ภาพรวม")
            
            filt_col1, filt_col2 = st.columns(2)
            
            unique_calc_zones = [str(z).upper() for z in main_df['Calculated_Zone'].unique() if pd.notna(z) and str(z).strip() != '']
            
            with filt_col1:
                zone_options = ["ทั้งหมด (Overall)"] + sorted(list(set(unique_calc_zones)))
                selected_zone = st.selectbox("📌 เลือก Zone", zone_options)
                
            with filt_col2:
                if selected_zone == "ทั้งหมด (Overall)":
                    temp_df = main_df
                else:
                    temp_df = main_df[main_df['Calculated_Zone'] == selected_zone]
                
                if 'Record by' in temp_df.columns:
                    unique_names = [str(n).strip() for n in temp_df['Record by'].unique() if pd.notna(n) and str(n).strip() != '']
                    name_options = ["ทั้งหมด (Overall)"] + sorted(list(set(unique_names)))
                else:
                    name_options = ["ทั้งหมด (Overall)"]
                    
                selected_name = st.selectbox("👤 เลือกชื่อผู้บันทึก (Record by)", name_options)
            
            df_large = main_df.copy()
            title_texts = []
            
            if selected_zone != "ทั้งหมด (Overall)":
                df_large = df_large[df_large['Calculated_Zone'] == selected_zone]
                title_texts.append(f"พื้นที่ {selected_zone}")
                
            if selected_name != "ทั้งหมด (Overall)":
                df_large = df_large[df_large['Record by'].astype(str).str.strip() == selected_name]
                title_texts.append(f"ผู้บันทึก: {selected_name}")
                
            if not title_texts:
                title_large = "ภาพรวมทั้งหมด (Overall)"
            else:
                title_large = " | ".join(title_texts)
                
            st.markdown(f"#### 📊 สรุปรายละเอียด: {title_large}")
            
            comp_count = len(df_large[df_large['Status_Clean'] == 'completed'])
            prog_count = len(df_large[df_large['Status_Clean'] == 'in progress'])
            
            m_c1, m_c2, m_c3, m_c4, m_c5 = st.columns(5)
            m_c1.info(f"✅ **Completed:**\n\n{comp_count} งาน")
            m_c2.warning(f"⏳ **In Progress:**\n\n{prog_count} งาน")
            m_c3.success(f"**12C:**\n\n{df_large[df_large['Cable_Type_Clean'] == '12c']['Distance(M)'].sum():,.0f} ม.")
            m_c4.error(f"**24C:**\n\n{df_large[df_large['Cable_Type_Clean'] == '24c']['Distance(M)'].sum():,.0f} ม.")
            m_c5.success(f"**96C:**\n\n{df_large[df_large['Cable_Type_Clean'] == '96c']['Distance(M)'].sum():,.0f} ม.")

            map_data_large = st_folium(create_map(df_large), width="100%", height=600, key=f"single_map_{selected_zone}_{selected_name}")

            curr_large = map_data_large.get("last_object_clicked_tooltip") if map_data_large else None

            if curr_large != st.session_state.prev_clicks['large']:
                st.session_state.current_filter = curr_large
                st.session_state.prev_clicks['large'] = curr_large

            clicked_tooltip = st.session_state.current_filter
            st.markdown("---")
            
            if clicked_tooltip:
                st.subheader("🔍 รายละเอียดข้อมูลที่คุณเลือกจากแผนที่")
                df_display = df_large[df_large['Map_Tooltip'] == clicked_tooltip]
                if st.button("❌ ยกเลิกการเลือก"):
                    st.session_state.current_filter = None
                    st.rerun()
            else:
                st.subheader("📄 รายละเอียดข้อมูลทั้งหมด")
                df_display = df_large
                
            st.dataframe(df_display.drop(columns=['Map_Tooltip', 'Calculated_Zone', 'Status_Clean', 'Cable_Type_Clean'], errors='ignore'))

elif st.session_state.view_mode == 'overlap':
    
    selected_group_cable = None
    selected_bg_color = None
    selected_row = None
    zoom_level = 11
    
    if "ai_table" in st.session_state and st.session_state.get('is_ai_mode', False):
        selected_rows = st.session_state["ai_table"]["selection"]["rows"]
        if len(selected_rows) > 0 and st.session_state.ai_display_df is not None:
            selected_idx = selected_rows[0]
            selected_row = st.session_state.ai_display_df.iloc[selected_idx]
            selected_group_cable = selected_row.get('Group_Total_Cable', 0)
            selected_bg_color = selected_row.get('BgColor', '')
    
    if st.session_state.get('is_ai_mode', False):
        st.subheader("🤖 ผลวิเคราะห์ยุบรวมแผนคร่อมยาว (AI Suggestion)")
        ai_df = st.session_state.ai_results_df
        
        if selected_group_cable is not None and selected_group_cable > 0 and selected_bg_color != '':
            st.success(f"💡 แนะนำคร่อมยาวเพื่อลดงาน Reduce | ประมาณการสาย Cable เฉพาะกลุ่มที่คลิกเลือก: **{selected_group_cable:,.2f} เมตร**")
        else:
            total_cable_all = ai_df[ai_df['Merged_Count'] > 1]['Estimated_Cable_Meters'].sum() if ai_df is not None and not ai_df.empty else 0
            if total_cable_all > 0:
                st.success(f"💡 แนะนำคร่อมยาวเพื่อลดงาน Reduce | ประมาณการสาย Cable รวมแผนที่ยุบรวมทั้งหมด: **{total_cable_all:,.2f} เมตร** (คลิกเลือกกลุ่มที่ตารางด้านล่างเพื่อดูระยะและซูมเฉพาะกลุ่ม)")
    else:
        st.subheader("⚠️ ผลการตรวจสอบการทับซ้อน (Overlap Check)")
    
    if st.button("⬅️ กลับไปหน้า Dashboard"):
        st.session_state.view_mode = 'dashboard'
        st.rerun()

    # --- คำนวณจุดกึ่งกลางและ Zoom ---
    if st.session_state.get('is_ai_mode', False):
        if st.session_state.ai_display_df is not None and not st.session_state.ai_display_df.empty:
            center_lat = st.session_state.ai_display_df['Lat_Start'].mean()
            center_lon = st.session_state.ai_display_df['Lon_Start'].mean()
        elif not main_df.empty:
            center_lat = main_df['Lat_Start'].mean()
            center_lon = main_df['Lon_Start'].mean()
        else:
            center_lat, center_lon = 15.8700, 101.5000
            
        if selected_row is not None:
            center_lat = (selected_row['Lat_Start'] + selected_row['Lat_Stop']) / 2
            center_lon = (selected_row['Lon_Start'] + selected_row['Lon_Stop']) / 2
            zoom_level = 15
    else:
        if st.session_state.overlap_lines_to_draw:
            center_lat = st.session_state.overlap_lines_to_draw[0]['start'][0]
            center_lon = st.session_state.overlap_lines_to_draw[0]['start'][1]
        elif not main_df.empty:
            center_lat = main_df['Lat_Start'].mean()
            center_lon = main_df['Lon_Start'].mean()
        else:
            center_lat, center_lon = 15.8700, 101.5000
            
        if "overlap_table" in st.session_state:
            overlap_sel_rows = st.session_state["overlap_table"]["selection"]["rows"]
            if len(overlap_sel_rows) > 0 and st.session_state.overlap_results_df is not None and not st.session_state.overlap_results_df.empty:
                overlap_sel_idx = overlap_sel_rows[0]
                selected_row = st.session_state.overlap_results_df.iloc[overlap_sel_idx]
                center_lat = (selected_row['Base_Lat_Start'] + selected_row['Base_Lat_Stop']) / 2
                center_lon = (selected_row['Base_Lon_Start'] + selected_row['Base_Lon_Stop']) / 2
                zoom_level = 15
            
    m_overlap = folium.Map(location=[center_lat, center_lon], zoom_start=zoom_level)
    
    if not main_df.empty:
        for index, row in main_df.iterrows():
            route_coords = get_road_route(row['Lat_Start'], row['Lon_Start'], row['Lat_Stop'], row['Lon_Stop'])
            cable_type = str(row.get('Cable Type', '')).strip().lower()
            if cable_type == '24c': line_color = "red"
            elif cable_type == '12c': line_color = "pink"
            elif cable_type == '96c': line_color = "green"
            else: line_color = "gray"
            folium.PolyLine(locations=route_coords, color=line_color, weight=3, opacity=0.2, tooltip=f"MAIN: {row['Map_Tooltip']}").add_to(m_overlap)
        
    # --- ตรวจสอบโหมดวาดเส้น (AI / Manual) ---
    if st.session_state.get('is_ai_mode', False):
        for idx, plan in st.session_state.ai_results_df.iterrows():
            if plan['Merged_Count'] > 1:
                
                # 1. วาดเส้นงานเดิม (สีส้ม) บนถนน
                for job in plan['Original_Jobs']:
                    route = get_road_route(job['Lat_Start'], job['Lon_Start'], job['Lat_Stop'], job['Lon_Stop'])
                    
                    folium.PolyLine(locations=route, color="orange", weight=5, tooltip=f"งานเดิม: {job.get('Name', 'Job')}").add_to(m_overlap)
                    
                    folium.Marker(
                        location=[job['Lat_Start'], job['Lon_Start']], 
                        icon=folium.Icon(color="blue", icon="info-sign"),
                        tooltip=f"Start: {job.get('Name')}"
                    ).add_to(m_overlap)
                    
                    folium.Marker(
                        location=[job['Lat_Stop'], job['Lon_Stop']], 
                        icon=folium.Icon(color="red", icon="info-sign"),
                        tooltip=f"Stop: {job.get('Name')}"
                    ).add_to(m_overlap)

                # 2. วาดเส้นคร่อมยาวเต็มเส้น (สีม่วงปะ) 
                full_route = plan.get('Full_Route', [])
                if full_route:
                    offset_full_merge_rt = offset_route(full_route, offset_deg=0.0004) # ขยับออกด้านข้าง
                    
                    folium.PolyLine(
                        locations=offset_full_merge_rt, 
                        color="purple", 
                        weight=6, 
                        dash_array='10, 15', 
                        tooltip=f"แผนคร่อมยาว (คลุมทุกจุด) | ระยะประเมินสายรวม: {plan['Estimated_Cable_Meters']:,.2f} ม."
                    ).add_to(m_overlap)
                    
        # ไฮไลต์เส้นที่ผู้ใช้จิ้มเลือกจากตาราง 
        if selected_row is not None:
            sel_route = get_road_route(selected_row['Lat_Start'], selected_row['Lon_Start'], selected_row['Lat_Stop'], selected_row['Lon_Stop'])
            folium.PolyLine(locations=sel_route, color="blue", weight=8, opacity=0.8, tooltip=f"📍 ที่เลือก: {selected_row['Name']}").add_to(m_overlap)
            
    else:
        for line in st.session_state.overlap_lines_to_draw:
            chk_route = get_road_route(line['start'][0], line['start'][1], line['stop'][0], line['stop'][1])
            offset_rt = offset_route(chk_route)
            
            folium.PolyLine(
                locations=offset_rt,
                color="yellow",
                weight=6,
                opacity=0.8,
                dash_array='10, 10',
                tooltip=f"CHECK: {line['name']}"
            ).add_to(m_overlap)
            
        if selected_row is not None and not main_df.empty:
            base_route = get_road_route(selected_row['Base_Lat_Start'], selected_row['Base_Lon_Start'], selected_row['Base_Lat_Stop'], selected_row['Base_Lon_Stop'])
            cable_type = str(selected_row.get('Base_Cable_Type', '')).strip().lower()
            line_color = "red" if cable_type == '24c' else "pink" if cable_type == '12c' else "green" if cable_type == '96c' else "gray"
            folium.PolyLine(locations=base_route, color=line_color, weight=6, opacity=1.0, tooltip=f"📍 MAIN (ที่เลือก)").add_to(m_overlap)

            chk_route = get_road_route(selected_row['Check_Lat_Start'], selected_row['Check_Lon_Start'], selected_row['Check_Lat_Stop'], selected_row['Check_Lon_Stop'])
            offset_rt = offset_route(chk_route)
            folium.PolyLine(locations=offset_rt, color="yellow", weight=7, opacity=1.0, dash_array='10, 10', tooltip=f"📍 CHECK (ที่เลือก)").add_to(m_overlap)

    st_folium(m_overlap, width="100%", height=500, key="map_overlap")
    
    # --- ตารางแสดงผล ---
    if st.session_state.get('is_ai_mode', False):
        st.markdown("### 📋 ตารางสรุปแผนงานที่ AI แนะนำให้ยุบรวม")
        disp_df = st.session_state.ai_display_df
        
        if disp_df is not None and not disp_df.empty:
            display_cols = ['Road_Name', 'Name', 'Remark', 'Lat_Start', 'Lon_Start', 'Lat_Stop', 'Lon_Stop']
            
            def highlight_rows(row):
                bg = row['BgColor']
                return [f"background-color: {bg}" if bg else "" for _ in row]
            
            styled_df = disp_df.style.apply(highlight_rows, axis=1)
            
            st.dataframe(
                styled_df,
                column_order=display_cols, 
                use_container_width=True,
                on_select="rerun",
                selection_mode="single-row",
                key="ai_table"
            )
            
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                disp_df[display_cols].to_excel(writer, index=False, sheet_name='AI_Merge_Plan')
            
            st.sidebar.markdown("**Export AI Plan**")
            st.sidebar.download_button(
                label="📥 Export AI Merged Plan (Excel)",
                data=output.getvalue(),
                file_name="AI_Merged_Plan.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )
    else:
        st.markdown("### 📋 ตารางสรุปสายที่อยู่ในระยะ (<= 500m)")
        res_df = st.session_state.overlap_results_df
        if res_df is not None and not res_df.empty:
            display_cols = ['Checked_Name', 'Base_Tracker_ID', 'Base_Status', 'Base_Site_Code', 'Base_Site_B', 'Base_Cable_Type', 'Remark']
            
            st.dataframe(
                res_df[display_cols], 
                use_container_width=True,
                on_select="rerun",
                selection_mode="single-row",
                key="overlap_table"
            )
            
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                res_df[display_cols].to_excel(writer, index=False, sheet_name='Overlap_Results')
            
            st.sidebar.markdown("**Export Result**")
            st.sidebar.download_button(
                label="📥 Export File (Excel)",
                data=output.getvalue(),
                file_name="Overlap_Check_Results.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )
        else:
            st.success("✅ ไม่พบเส้นทางที่ทับซ้อนหรืออยู่ในระยะ 500 เมตรจากเส้นทางเดิม (หรือคุณยังไม่ได้อัปโหลดไฟล์ Main)")
