import streamlit as st
from kiwipiepy import Kiwi
from PIL import Image, ImageDraw, ImageFont
import io
import os
import urllib.request
import urllib.parse
import json
import base64
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET

# --- 1. 형태소 분석기 로드 ---
@st.cache_resource
def load_kiwi():
    return Kiwi()

kiwi = load_kiwi()

# --- 형광펜 색상 ---
PASSIVE_COLOR = "<span style='background-color: #ffcccc; padding: 2px 4px; border-radius: 4px; font-weight: bold;'>{text}</span>"
CAUSATIVE_COLOR = "<span style='background-color: #fff2cc; padding: 2px 4px; border-radius: 4px; font-weight: bold;'>{text}</span>"
DIRECT_COLOR = "<span style='background-color: #cce5ff; padding: 2px 4px; border-radius: 4px; font-weight: bold;'>{text}</span>"
INDIRECT_COLOR = "<span style='background-color: #d4edda; padding: 2px 4px; border-radius: 4px; font-weight: bold;'>{text}</span>"

# =====================================================================
# 🚨 2. 실전 배포용 비밀 금고(Secrets) 안전 연동 🚨
# =====================================================================
try:
    TEACHER_API_KEY = st.secrets["KOREAN_API_KEY"]
except Exception:
    TEACHER_API_KEY = None  

try:
    GITHUB_TOKEN = st.secrets["GITHUB_TOKEN"]
    GITHUB_REPO = st.secrets["GITHUB_REPO"] 
except Exception:
    GITHUB_TOKEN = "" 
    GITHUB_REPO = ""  

# --- 국립국어원 사전 API 통신 함수 ---
def check_dict_api(word):
    if not TEACHER_API_KEY: return [] 
    url = f"https://stdict.korean.go.kr/api/search.do?key={TEACHER_API_KEY}&q={urllib.parse.quote(word)}"
    try:
        req = urllib.request.Request(url)
        res = urllib.request.urlopen(req)
        xml_data = res.read().decode('utf-8')
        root = ET.fromstring(xml_data)
        
        pos_tags = []
        for item in root.findall('.//item'):
            pos_el = item.find('.//pos')
            def_el = item.find('.//sense/definition')
            
            pos_text = pos_el.text.strip() if pos_el is not None and pos_el.text else ""
            def_text = def_el.text.strip() if def_el is not None and def_el.text else ""
            
            if "피동사" in def_text: pos_tags.append("피동사")
            elif "사동사" in def_text: pos_tags.append("사동사")
            elif pos_text: pos_tags.append(pos_text)
                
        return pos_tags 
    except Exception:
        return []

# --- 한글 폰트 자동 다운로더 ---
@st.cache_resource
def get_korean_font(size=20):
    font_path = "NanumGothic.ttf"
    if not os.path.exists(font_path):
        try:
            url = "https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf"
            urllib.request.urlretrieve(url, font_path)
        except Exception:
            pass
            
    if os.path.exists(font_path):
        return ImageFont.truetype(font_path, size)
    return ImageFont.load_default()

def is_yang_vowel(word_chunk):
    if not word_chunk: return False
    last_char = word_chunk[-1]
    if '가' <= last_char <= '힣':
        char_code = ord(last_char) - ord('가')
        jung_seong_idx = (char_code % 588) // 28
        return jung_seong_idx in [0, 2, 8, 9, 12]
    return False

# --- 깃허브 업로드 함수 (경로 및 파일명 오류 완벽 수정 버전) ---
def upload_png_to_github(img_bytes, student_id, student_name):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False, "⚠️ 아직 선생님의 온라인 수합 폴더가 연결되지 않았습니다."
        
    # 대한민국 표준시(KST) 동기화
    KST = timezone(timedelta(hours=9))
    timestamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    
    # 💡 [핵심 조치] 파일명만 순수하게 추출하여 인터넷 주소용(ASCII)으로 안전 인코딩!
    raw_filename = f"{student_id}_{student_name}_{timestamp}.png"
    safe_filename = urllib.parse.quote(raw_filename)
    
    # submissions/ 파란 폴더 경로와 인코딩된 파일명을 매끄럽게 연결해 에러를 원천 차단합니다.
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/submissions/{safe_filename}"
    
    encoded_img = base64.b64encode(img_bytes).decode('utf-8')
    
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
    }
    data = {
        "message": f"과제 제출: {student_id} {student_name}",
        "content": encoded_img
    }
    try:
        data_bytes = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(url, headers=headers, data=data_bytes, method='PUT')
        with urllib.request.urlopen(req) as response:
            if response.status in [200, 201]:
                return True, f"✨ 제출 성공! 선생님의 [과제 확인 폴더]로 숙제 파일이 안전하게 전송되었습니다. 고생했어요!"
            else:
                return False, f"❌ 전송 실패 (GitHub 서버 응답 코드: {response.status})"
    except Exception as e:
        return False, f"❌ 폴더 전송 실패: {str(e)}"

def analyze_sentence(text, target_type):
    if not TEACHER_API_KEY:
        return False, "", "", [], "🚨 [시스템 안내] 표준국어대사전 API 키가 연결되지 않았습니다."

    if not text.strip(): return False, "", "문장이 입력되지 않았습니다.", [], "문장을 입력해 주세요."
    tokens = kiwi.tokenize(text)
    has_quotes = '"' in text or '“' in text or '”' in text
    
    display_html, display_plain, base_forms = [], [], []
    found_target, api_rejected, error_msg = False, False, ""
    last_verb_stem = ""
    
    i = 0
    while i < len(tokens):
        token = tokens[i]
        form, tag = token.form, token.tag
        
        if tag.startswith('V'): last_verb_stem = form

        if i < len(tokens) - 1 and tag == 'EC' and form in ['어', '아'] and tokens[i+1].tag == 'VX' and tokens[i+1].form == '지':
            if target_type == 2: found_target = True
            j_vowel = "아" if is_yang_vowel(last_verb_stem) else "어"
            jida_form = f"-{j_vowel}지다"
            display_html.append(PASSIVE_COLOR.format(text=f"{jida_form}(피동)")); display_plain.append(f"[{jida_form}(피동)]")
            i += 2; continue
            
        if tag.startswith('V') and form.endswith('지') and len(form) >= 2:
            root = form[:-1] 
            if target_type == 2: found_target = True
            jtext = "-아지다" if is_yang_vowel(root) else "-어지다"
            display_html.append(root); display_plain.append(root)
            display_html.append(PASSIVE_COLOR.format(text=f"{jtext}(피동)")); display_plain.append(f"[{jtext}(피동)]")
            bword = form + "다" 
            base_forms.append({"word": bword, "valid": check_dict_api(bword)})
            i += 1; continue
            
        if tag == 'XSV' and form == '되':
            if target_type == 2: found_target = True
            display_html.append(PASSIVE_COLOR.format(text="-되다(피동)")); display_plain.append("[-되다(피동)]")
            if i > 0:
                bword = tokens[i-1].form + "되다"
                base_forms.append({"word": bword, "valid": check_dict_api(bword)})
            i += 1; continue
            
        if tag.startswith('V') and len(form) >= 2 and form[-1] in ['이', '히', '리', '기']:
            if target_type == 1: found_target = True 
            root, suffix = form[:-1], form[-1]
            last_verb_stem = form 
            
            display_html.append(f"{root}-"); display_plain.append(f"{root}-")
            bword = form + "다"
            base_forms.append({"word": bword, "valid": check_dict_api(bword)})
            
            display_html.append(PASSIVE_COLOR.format(text=f"-{suffix}-(피동 접사)")); display_plain.append(f"[-{suffix}-(피동 접사)]")
            i += 1; continue
            
        if form in ['"', '“', '”']: display_html.append(DIRECT_COLOR.format(text=form)); display_plain.append(f"[{form}]"); i += 1; continue
        if tag == 'JKQ' and form in ['라고', '이라고', '하고'] and has_quotes:
            if target_type == 3: found_target = True
            display_html.append(DIRECT_COLOR.format(text=f"{form}(직접 인용)")); display_plain.append(f"[{form}(직접 인용)]"); i += 1; continue
            
        if tag == 'EC' and form in ['라', '라고'] and i > 0 and tokens[i-1].tag == 'VCP':
            if target_type == 4 and not has_quotes: found_target = True
            if display_html and tokens[i-1].form == '이': display_html.pop(); display_plain.pop()
            display_html.append("이다"); display_plain.append("이다")
            display_html.append(INDIRECT_COLOR.format(text="-고(간접 인용)")); display_plain.append("[-고(간접 인용)]"); i += 1; continue
            
        if tag == 'EC' and form in ['다고', '자고', '냐고', '라고'] and not has_quotes:
            if target_type == 4: found_target = True
            display_html.append(f"-{form[:-1]}"); display_plain.append(f"-{form[:-1]}")
            display_html.append(INDIRECT_COLOR.format(text="-고(간접 인용)")); display_plain.append("[-고(간접 인용)]"); i += 1; continue
            
        if tag == 'JKQ' and form == '고' and not has_quotes:
            if target_type == 4: found_target = True
            display_html.append(INDIRECT_COLOR.format(text="-고(간접 인용)")); display_plain.append("[-고(간접 인용)]"); i += 1; continue
            
        if tag in ['EC', 'EP', 'EF'] and last_verb_stem:
            if is_yang_vowel(last_verb_stem):
                if form == '어': form = '아'
                elif form == '었': form = '았'
                elif form == '어서': form = '아서'
                elif form == '어도': form = '아도'
            else:
                if form == '아': form = '어'
                elif form == '았': form = '었'
                elif form == '어서': form = '어서'
                elif form == '아도': form = '어도'

        if tag.startswith('V'): display_html.append(f"{form}-"); display_plain.append(f"{form}-")
        elif tag == 'EP': display_html.append(f"-{form}-"); display_plain.append(f"-{form}-")
        elif tag == 'EF': display_html.append(f"-{form}"); display_plain.append(f"-{form}")
        else:
            if tag not in ['JKQ']: display_html.append(form); display_plain.append(form)
        i += 1

    if found_target and base_forms:
        for b in base_forms:
            plist = b['valid'] 
            if not plist:
                api_rejected = True; error_msg = f"❌ '{b['word']}'는 표준국어대사전에 없는 단어예요!"; break
            
            if target_type == 1 and plist:
                if '피동사' not in plist and '사동사' in plist:
                    api_rejected = True; error_msg = f"❌ '{b['word']}'는 누군가에게 시키는 '사동사'예요. 당하는 '피동 표현'으로 다시 쓰세요!"; break

    if api_rejected: found_target = False
    elif not found_target:
        if target_type == 1: error_msg = "❌ [-이/히/리/기-] 피동 접사가 포함된 피동사가 없습니다!"
        elif target_type == 2: error_msg = "❌ [-아/어지다] 또는 [-되다] 피동 표현이 없습니다."
        elif target_type == 3: error_msg = "❌ 큰따옴표가 포함된 직접 인용 표현이 없습니다."
        elif target_type == 4: error_msg = "❌ 조사 '-고'가 쓰인 간접 인용 표현이 없습니다. (큰따옴표 삭제)"

    for b in base_forms:
        if isinstance(b['valid'], list):
            if target_type == 1: b['valid'] = ('피동사' in b['valid'])
            else: b['valid'] = len(b['valid']) > 0

    return found_target, " + ".join(display_html), " + ".join(display_plain), base_forms, error_msg

def render_base_form_links(bases):
    if not bases: return ""
    links = []
    for b in bases:
        w, v = b['word'], b['valid']
        status = "<span style='color:green; font-weight:bold;'>(✅ 사전 확인 완료)</span>" if v else "<span style='color:red; font-weight:bold;'>(❌ 오답)</span>"
        links.append(f"<a href='https://stdict.korean.go.kr/search/searchResult.do?pageSize=10&searchKeyword={urllib.parse.quote(w)}' target='_blank' style='text-decoration:none;'>🔍 <b>{w}</b></a> {status}")
    return "👉 [단어 사전에서 보기]: " + " | ".join(links)

def create_report_png(student_id, student_name, sentences, plain_results, base_forms_list):
    img = Image.new("RGB", (850, 750), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    ftitle, fsub, fbody = get_korean_font(26), get_korean_font(18), get_korean_font(15)
    
    draw.rectangle([(20, 20), (830, 730)], outline=(120, 120, 120), width=2)
    draw.text((40, 45), "📝 피동 표현, 인용 표현 작성 결과지", fill=(30, 30, 30), font=ftitle)
    draw.text((40, 95), f"학번: {student_id}   |   이름: {student_name}", fill=(80, 80, 80), font=fsub)
    draw.line([(40, 130), (810, 130)], fill=(0, 0, 0), width=2)
    
    labels = ["1. 피동 표현 (-이-, -히-, -리-, -기-)", "2. 피동 표현 (-아/어지다 또는 -되다)", "3. 직접 인용 표현", "4. 간접 인용 표현"]
    y_pos = 150
    for idx in range(4):
        draw.text((40, y_pos), labels[idx], fill=(0, 102, 204), font=fsub); y_pos += 26
        draw.text((60, y_pos), f"작성 문장: {sentences[idx]}", fill=(0, 0, 0), font=fbody); y_pos += 22
        draw.text((60, y_pos), f"형태소 분석: {plain_results[idx]}", fill=(220, 50, 50), font=fbody)
        
        bases = base_forms_list[idx]
        if bases:
            btexts = [f"{b['word']} {'[확인완료]' if b['valid'] else '[미등재/오답]'}" for b in bases]
            y_pos += 22; draw.text((60, y_pos), f"▶ 기본형: " + ", ".join(btexts), fill=(0, 120, 50), font=fbody)
        y_pos += 45
    draw.text((40, 695), "* 본 결과지는 프로그램에 의해 자동 채점된 과제 증빙 파일입니다.", fill=(130, 130, 130), font=fbody)
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    return img_byte_arr.getvalue()


# ==========================================
# 💡 UI 및 기억 수첩(Session State) 제어 구간
# ==========================================
st.set_page_config(page_title="국어과 과제 제출함", layout="centered")

# 컴퓨터 수첩(Session State) 초기화
if "passed_all" not in st.session_state:
    st.session_state.passed_all = False
if "report_png" not in st.session_state:
    st.session_state.report_png = None

# 글자를 1글자라도 수정하면 수첩 합격 여부 리셋
def reset_pass_state():
    st.session_state.passed_all = False
    st.session_state.report_png = None

def clear_q(key): 
    st.session_state[key] = ""
    reset_pass_state()

for k in ['q1', 'q2', 'q3', 'q4']:
    if k not in st.session_state: st.session_state[k] = ""

st.title("🎓 국어과 피동·인용 표현 과제 제출함")
st.markdown("학번과 이름을 정확히 적고 4가지 문장을 완성하세요. 4개 모두 초록색 불(통과)이 들어오면 **선생님 폴더로 바로 제출**할 수 있습니다!")
st.markdown("---")

st.subheader("👤 학생 정보")
col1, col2 = st.columns(2)
with col1: s_id = st.text_input("학번 (예: 20101):", key="s_id", placeholder="예: 20101", on_change=reset_pass_state)
with col2: s_name = st.text_input("이름 (예: 홍길동):", key="s_name", placeholder="예: 김민수", on_change=reset_pass_state)

st.markdown("---")
st.subheader("✍️ 과제 작성란")

c1, b1 = st.columns([85, 15]); c1.text_input("1️⃣ 피동 표현 (-이-, -히-, -리-, -기-)", key="q1", placeholder="예: 토끼가 사자에게 발목을 잡혔다.", on_change=reset_pass_state); b1.write(""); b1.write(""); b1.button("🔄 지우기", key="btn1", on_click=clear_q, args=("q1",))
c2, b2 = st.columns([85, 15]); c2.text_input("2️⃣ 피동 표현 (-아/어지다 또는 -되다)", key="q2", placeholder="예: 책상 위에 있던 우유가 쏟아졌다.", on_change=reset_pass_state); b2.write(""); b2.write(""); b2.button("🔄 지우기", key="btn2", on_click=clear_q, args=("q2",))
c3, b3 = st.columns([85, 15]); c3.text_input("3️⃣ 직접 인용 표현", key="q3", placeholder="예: 민수가 나에게 \"매점 가자.\"라고 말했다.", on_change=reset_pass_state); b3.write(""); b3.write(""); b3.button("🔄 지우기", key="btn3", on_click=clear_q, args=("q3",))
c4, b4 = st.columns([85, 15]); c4.text_input("4️⃣ 간접 인용 표현", key="q4", placeholder="예: 선생님께서 내일 체육복을 입고 오라고 하셨다.", on_change=reset_pass_state); b4.write(""); b4.write(""); b4.button("🔄 지우기", key="btn4", on_click=clear_q, args=("q4",))

st.markdown("---")

# 1단계 버튼: 채점 수행
if st.button("🚀 내 과제 채점해보기"):
    if not s_id.strip() or not s_name.strip(): st.error("⚠️ 학번과 이름을 먼저 입력해 주세요!")
    elif not (st.session_state.q1 and st.session_state.q2 and st.session_state.q3 and st.session_state.q4): st.error("⚠️ 4개 문항 중 아직 안 쓴 칸이 있어요.")
    else:
        ok1, h1, p1, b1, e1 = analyze_sentence(st.session_state.q1, 1)
        ok2, h2, p2, b2, e2 = analyze_sentence(st.session_state.q2, 2)
        ok3, h3, p3, b3, e3 = analyze_sentence(st.session_state.q3, 3)
        ok4, h4, p4, b4, e4 = analyze_sentence(st.session_state.q4, 4)
        
        st.subheader("🔍 채점 결과")
        
        st.markdown(f"**[1번]** {h1}", unsafe_allow_html=True)
        if ok1: st.success("✔️ 통과!")
        else: st.error(e1)
        if b1: st.markdown(render_base_form_links(b1), unsafe_allow_html=True)
        
        st.markdown(f"<br>**[2번]** {h2}", unsafe_allow_html=True)
        if ok2: st.success("✔️ 통과!")
        else: st.error(e2)
        if b2: st.markdown(render_base_form_links(b2), unsafe_allow_html=True)
        
        st.markdown(f"<br>**[3번]** {h3}", unsafe_allow_html=True)
        if ok3: st.success("✔️ 통과!")
        else: st.error(e3)
        
        st.markdown(f"<br>**[4번]** {h4}", unsafe_allow_html=True)
        if ok4: st.success("✔️ 통과!")
        else: st.error(e4)
        
        if ok1 and ok2 and ok3 and ok4:
            st.balloons()
            st.success("🎉 완벽합니다! 4개 문항을 모두 맞혔습니다.")
            
            png_data = create_report_png(s_id, s_name, [st.session_state.q1, st.session_state.q2, st.session_state.q3, st.session_state.q4], [p1, p2, p3, p4], [b1, b2, b3, b4])
            
            st.session_state.passed_all = True
            st.session_state.report_png = png_data
        else:
            reset_pass_state()

# 2단계 버튼: 깃허브 제출 및 다운로드 (금붕어 리런 새로고침 방어 완료)
if st.session_state.passed_all and st.session_state.report_png:
    st.markdown("---")
    st.subheader("🚀 선생님 폴더로 최종 제출")
    st.write("아래 파란색 버튼을 누르면 내 과제 파일이 **선생님의 확인 폴더로 자동 전송**됩니다.")
    
    if st.button("📤 [선생님 폴더로 숙제 제출하기]", type="primary"):
        with st.spinner("선생님의 과제 폴더로 숙제 파일을 전송하고 있습니다... 슝! 🛸"):
            success, result_message = upload_png_to_github(st.session_state.report_png, s_id, s_name)
        
        if success: 
            st.success(result_message)
        else: 
            st.error(result_message)
            
    st.download_button("💾 내 컴퓨터에도 결과지 이미지 저장해두기", st.session_state.report_png, f"국어과제_{s_id}_{s_name}.png", "image/png")
