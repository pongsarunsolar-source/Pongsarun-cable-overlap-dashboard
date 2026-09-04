import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
import math
import io
import requests
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
def get_road_route(lat1, lon1, lat2, lon2):
    try:
        url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data['code'] == 'Ok':
                coords = data['routes'][0]['geometry']['coordinates']
                return [[lat, lon] for lon, lat in coords]
    except:
        pass
    return [[lat1, lon1], [lat2, lon2]]

def offset_route(route_coords, offset_deg=0.0002):
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

# ==========================================
# 3. ตั้งค่า Session State (เพิ่ม State สำหรับเก็บค่า Text Box)
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

# ป้องกันค่าใน Text box หายเวลาจอกระพริบ
if 'temp_start_val' not in st.session_state:
    st.session_state.temp_start_val = ""
if 'temp_stop_val' not in st.session_state:
    st.session_state.temp_stop_val = ""

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
    
    # ---------------------------------------------
    # ส่วนรับค่าพิกัดเริ่มต้น (Start)
    # ---------------------------------------------
    st.markdown("**📍 พิกัดเริ่มต้น (Start)**")
    
    # จัดเลย์เอาต์ปุ่ม GPS ให้อยู่ข้างกล่องข้อความ
    col_start_txt, col_start_gps = st.columns([5, 1])
    
    with col_start_txt:
        # กล่อง Text ให้ผู้ใช้พิมพ์แก้ได้อิสระ
        m_start_val = st.text_input(
            "Lat/Long Start", 
            value=st.session_state.temp_start_val, 
            placeholder="Lat, Lon", 
            key="m_start_input",
            label_visibility="collapsed"
        )
        # อัปเดต State ตลอดเวลาเผื่อพิมพ์แก้เอง
        st.session_state.temp_start_val = m_start_val
        
    with col_start_gps:
        # ปุ่มดึง GPS เฉพาะจุด Start
        loc_start = streamlit_geolocation()
        if loc_start and loc_start.get('latitude') is not None:
            # ถ้ายอมกดดึง GPS ให้เอาค่าใหม่ไปทับในกล่อง Text อัตโนมัติ
            new_start_gps = f"{loc_start['latitude']}, {loc_start['longitude']}"
            if new_start_gps != st.session_state.temp_start_val:
                 st.session_state.temp_start_val = new_start_gps
                 st.rerun()

    st.markdown("<hr style='margin: 10px 0;'>", unsafe_allow_html=True)
    
    # ---------------------------------------------
    # ส่วนรับค่าพิกัดสิ้นสุด (Stop)
    # ---------------------------------------------
    st.markdown("**📍 พิกัดสิ้นสุด (Stop)**")
    
    col_stop_txt, col_stop_gps = st.columns([5, 1])
    
    with col_stop_txt:
        m_stop_val = st.text_input(
            "Lat/Long Stop", 
            value=st.session_state.temp_stop_val, 
            placeholder="Lat, Lon", 
            key="m_stop_input",
            label_visibility="collapsed"
        )
        st.session_state.temp_stop_val = m_stop_val
        
    with col_stop_gps:
         loc_stop = streamlit_geolocation()
         if loc_stop and loc_stop.get('latitude') is not None:
             new_stop_gps = f"{loc_stop['latitude']}, {loc_stop['longitude']}"
             if new_stop_gps != st.session_state.temp_stop_val:
                 st.session_state.temp_stop_val = new_stop_gps
                 st.rerun()
    
    st.markdown("<hr style='margin: 10px 0;'>", unsafe_allow_html=True)
    
    # ---------------------------------------------
    # ปุ่มประมวลผล (Process)
    # ---------------------------------------------
    if st.button("Check Overlap", type="primary", use_container_width=True):
        if not main_df.empty and st.session_state.temp_start_val and st.session_state.temp_stop_val:
            try:
                s_parts = st.session_state.temp_start_val.split(',')
                e_parts = st.session_state.temp_stop_val.split(',')
                new_start = (float(s_parts[0].strip()), float(s_parts[1].strip()))
                new_stop = (float(e_parts[0].strip()), float(e_parts[1].strip()))
                
                overlaps = check_overlap(new_start, new_stop, main_df)
                st.session_state.overlap_lines_to_draw = [{'name': 'Manual Line', 'start': new_start, 'stop': new_stop}]
                
                if overlaps:
                    res_df = pd.DataFrame(overlaps)
                    res_df.insert(0, 'Checked_Name', 'Manual Line')
                    st.session_state.overlap_results_df = res_df
                else:
                    st.session_state.overlap_results_df = pd.DataFrame()
                
                st.session_state.view_mode = 'overlap'
            except Exception:
                st.sidebar.error("❌ รูปแบบพิกัดไม่ถูกต้อง กรุณากรอกแบบ 'Lat, Long' และคั่นด้วยลูกน้ำ")
        else:
            st.sidebar.warning("กรุณาอัปโหลดไฟล์ Main และกรอกพิกัดให้ครบทั้ง 2 ช่อง")

st.sidebar.markdown("**Import Check route Overlap (Excel)**")
uploaded_overlap_file = st.sidebar.file_uploader("อัปโหลดไฟล์ Excel (Overlap)", type=["xlsx"], key="overlap_upload")

if uploaded_overlap_file is not None and not main_df.empty:
    overlap_df = pd.read_excel(uploaded_overlap_file)
    overlap_df = clean_coords(overlap_df)
    
    if st.sidebar.button("Process Overlap File", type="primary", use_container_width=True):
        all_results = []
        lines_to_draw = []
        
        for idx, row in overlap_df.iterrows():
            new_start = (row['Lat_Start'], row['Lon_Start'])
            new_stop = (row['Lat_Stop'], row['Lon_Stop'])
            name = row.get('Name', f'Line_{idx}')
            
            lines_to_draw.append({'name': name, 'start': new_start, 'stop': new_stop})
            overlaps = check_overlap(new_start, new_stop, main_df)
            
            for ol in overlaps:
                ol['Checked_Name'] = name
                all_results.append(ol)
                
        st.session_state.overlap_lines_to_draw = lines_to_draw
        st.session_state.overlap_results_df = pd.DataFrame(all_results)
        st.session_state.view_mode = 'overlap'

# ==========================================
# 5. การแสดงผลหน้าจอหลัก (Main Content)
# ==========================================
st.title("Cable Replacement Dashboard")

if main_df.empty:
    st.info("👈 กรุณาอัปโหลดไฟล์ Excel ภาพรวมข้อมูล ที่ Sidebar ด้านซ้ายเพื่อเริ่มต้นใช้งาน")
else:
    if st.session_state.view_mode == 'dashboard':
        
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
                df_display = main_df[main_df['Map_Tooltip'] == clicked_tooltip]
                if st.button("❌ ยกเลิกการเลือก"):
                    st.session_state.current_filter = None
                    st.rerun()
            else:
                st.subheader("📄 รายละเอียดข้อมูลทั้งหมด")
                df_display = main_df
                
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
        st.subheader("⚠️ ผลการตรวจสอบการทับซ้อน (Overlap Check)")
        
        if st.button("⬅️ กลับไปหน้า Dashboard"):
            st.session_state.view_mode = 'dashboard'
            st.rerun()

        res_df = st.session_state.overlap_results_df
        
        zoom_level = 11
        center_lat = st.session_state.overlap_lines_to_draw[0]['start'][0] if st.session_state.overlap_lines_to_draw else main_df['Lat_Start'].mean()
        center_lon = st.session_state.overlap_lines_to_draw[0]['start'][1] if st.session_state.overlap_lines_to_draw else main_df['Lon_Start'].mean()
        
        selected_row = None
        if "overlap_table" in st.session_state:
            selected_rows = st.session_state["overlap_table"]["selection"]["rows"]
            if len(selected_rows) > 0 and res_df is not None and not res_df.empty:
                selected_idx = selected_rows[0]
                selected_row = res_df.iloc[selected_idx]
                
                center_lat = (selected_row['Base_Lat_Start'] + selected_row['Base_Lat_Stop']) / 2
                center_lon = (selected_row['Base_Lon_Start'] + selected_row['Base_Lon_Stop']) / 2
                zoom_level = 16
                
        m_overlap = folium.Map(location=[center_lat, center_lon], zoom_start=zoom_level)
        
        for index, row in main_df.iterrows():
            route_coords = get_road_route(row['Lat_Start'], row['Lon_Start'], row['Lat_Stop'], row['Lon_Stop'])
            
            cable_type = str(row.get('Cable Type', '')).strip().lower()
            if cable_type == '24c': line_color = "red"
            elif cable_type == '12c': line_color = "pink"
            elif cable_type == '96c': line_color = "green"
            else: line_color = "gray"
            folium.PolyLine(locations=route_coords, color=line_color, weight=3, opacity=0.3, tooltip=f"MAIN: {row['Map_Tooltip']}").add_to(m_overlap)
            
        for line in st.session_state.overlap_lines_to_draw:
            chk_route = get_road_route(line['start'][0], line['start'][1], line['stop'][0], line['stop'][1])
            offset_rt = offset_route(chk_route)
            
            folium.PolyLine(
                locations=offset_rt,
                color="yellow",
                weight=4,
                opacity=0.6,
                dash_array='10, 10',
                tooltip=f"CHECK: {line['name']}"
            ).add_to(m_overlap)
            
        if selected_row is not None:
            base_route = get_road_route(selected_row['Base_Lat_Start'], selected_row['Base_Lon_Start'], selected_row['Base_Lat_Stop'], selected_row['Base_Lon_Stop'])
            cable_type = str(selected_row.get('Base_Cable_Type', '')).strip().lower()
            line_color = "red" if cable_type == '24c' else "pink" if cable_type == '12c' else "green" if cable_type == '96c' else "gray"
            folium.PolyLine(locations=base_route, color=line_color, weight=6, opacity=1.0, tooltip=f"📍 MAIN (ที่เลือก)").add_to(m_overlap)

            chk_route = get_road_route(selected_row['Check_Lat_Start'], selected_row['Check_Lon_Start'], selected_row['Check_Lat_Stop'], selected_row['Check_Lon_Stop'])
            offset_rt = offset_route(chk_route)
            folium.PolyLine(locations=offset_rt, color="yellow", weight=7, opacity=1.0, dash_array='10, 10', tooltip=f"📍 CHECK (ที่เลือก)").add_to(m_overlap)

        st_folium(m_overlap, width="100%", height=500, key="map_overlap")
        
        st.markdown("### 📋 ตารางสรุปสายที่อยู่ในระยะ (<= 500m)")
        
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
            st.success("✅ ไม่พบเส้นทางที่ทับซ้อนหรืออยู่ในระยะ 500 เมตรจากเส้นทางเดิม")
