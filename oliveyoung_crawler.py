from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import time
import json
import os
from urllib.parse import urljoin
import requests
from multiprocessing import Pool, cpu_count
from functools import partial
import concurrent.futures
import signal
import sys
import re
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# 전역 변수로 크롤링된 데이터 저장
crawled_data = {
    'items': [],
    'last_page': 0,
    'last_product_index': 0
}

def save_progress():
    """현재까지의 크롤링 진행 상황을 저장"""
    with open('oliveyoung_products.json', 'w', encoding='utf-8') as f:
        json.dump(crawled_data, f, ensure_ascii=False, indent=2)
    print(f"\n진행 상황 저장 완료 (페이지: {crawled_data['last_page']}, 상품: {crawled_data['last_product_index']})")

def signal_handler(signum, frame):
    """시그널 핸들러: 프로그램 종료 시 진행 상황 저장"""
    print("\n프로그램이 중단되었습니다. 진행 상황을 저장합니다...")
    save_progress()
    sys.exit(0)

# 시그널 핸들러 등록
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def setup_driver():
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def download_image(url, save_path):
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return True
    except Exception as e:
        print(f"이미지 다운로드 실패: {url}")
        print(f"에러: {str(e)}")
        return False

def get_product_list(url):
    driver = setup_driver()
    driver.get(url)
    time.sleep(3)
    
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    products = []
    
    # data-number가 1부터 48까지인 모든 li 요소 선택
    product_items = []
    for num in range(1, 49):  # 1부터 48까지
        product_items.extend(soup.select(f'li[data-number="{num}"]'))
    
    for product in product_items:
        prd_info = product.select_one('div.prd_info')
        if prd_info:
            product_data = {}
            
            prd_name = prd_info.select_one('p.tx_name')
            product_data['name'] = prd_name.text.strip() if prd_name else None
            
            brand = prd_info.select_one('span.tx_brand')
            product_data['brand'] = brand.text.strip() if brand else None
            
            price_tag = prd_info.select_one('p.prd_price span.tx_cur span.tx_num')
            price = 0
            if price_tag:
                price_str = price_tag.text.strip().replace(',', '').replace('원', '')
                try:
                    price = int(price_str)
                except:
                    price = 0
            product_data['price'] = price
            
            img = prd_info.select_one('img')
            product_data['thumbnailUrls'] = [img['src']] if img and 'src' in img.attrs else []
            
            link = prd_info.select_one('a.prd_thumb')
            detail_url = link['href'] if link and 'href' in link.attrs else None
            if detail_url:
                detail_url = urljoin('https://www.oliveyoung.co.kr', detail_url)
            product_data['detailUrl'] = detail_url
            
            products.append(product_data)
    
    driver.quit()
    return products

def get_product_detail_info(detail_url):
    driver = setup_driver()
    driver.get(detail_url)
    time.sleep(2)

    # 1. 상품상세 이미지: 진입 직후(기본 탭)에서 추출
    image_urls = []
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    detail_div = soup.select_one('div.tabConts.prd_detail_cont.show')
    if detail_div:
        for img in detail_div.find_all('img'):
            src = img.get('src')
            data_src = img.get('data-src')
            if src and src.startswith('http') and not src.startswith('data:image'):
                image_urls.append(src)
            elif data_src and data_src.startswith('http') and not data_src.startswith('data:image'):
                image_urls.append(data_src)

    # 2. '구매정보' 탭 클릭 후 제조국/제조업자 추출
    manufacturer = None
    country0f0rigin = None
    try:
        buyinfo_tab = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "buyInfo"))
        )
        buyinfo_tab.click()
        time.sleep(2)
    except Exception as e:
        print("구매정보 탭 클릭 실패:", e)

    soup2 = BeautifulSoup(driver.page_source, 'html.parser')
    buyinfo_div = soup2.select_one('div.tabConts.prd_detail_cont.show')
    if buyinfo_div:
        artc_info = buyinfo_div.select_one('div#artcInfo')
        if artc_info:
            for dl in artc_info.select('dl.detail_info_list'):
                dt = dl.find('dt')
                dd = dl.find('dd')
                if not dt or not dd:
                    continue
                dt_text = dt.get_text(strip=True)
                dd_text = dd.get_text(strip=True)
                if '화장품제조업자' in dt_text:
                    m = re.search(r'화장품제조업자\s*[:：]\s*([^/]+)', dd_text)
                    if m:
                        manufacturer = m.group(1).strip()
                    else:
                        manufacturer = dd_text.strip()
                elif '제조국' in dt_text:
                    country0f0rigin = dd_text.strip()

    driver.quit()
    return {
        'detailImageUrls': image_urls,
        'manufacturer': manufacturer,
        'country0f0rigin': country0f0rigin
    }

def get_total_pages(url):
    driver = setup_driver()
    driver.get(url)
    time.sleep(3)
    
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    pagination = soup.select_one('div.pageing')
    if pagination:
        page_numbers = pagination.select('a')
        if page_numbers:
            last_page = max([int(page.text.strip()) for page in page_numbers if page.text.strip().isdigit()])
            driver.quit()
            return last_page
    
    driver.quit()
    return 1

def process_page(page, base_url, params):
    print(f"\n{page}페이지 크롤링 중...")
    page_url = f"{base_url}?{'&'.join([f'{k}={v}' for k, v in params.items()])}&pageIdx={page}"
    products = get_product_list(page_url)
    print(f"{page}페이지: {len(products)}개 상품 크롤링 완료")
    
    # 진행 상황 업데이트
    crawled_data['last_page'] = page
    if page % 2 == 0:  # 2페이지마다 진행 상황 저장
        save_progress()
    
    return products

def process_product_images(args):
    idx, product, total_products = args
    if product['detailUrl']:
        print(f"\n상품 {idx}/{total_products}의 상세 이미지 크롤링 중...")
        detail_info = get_product_detail_info(product['detailUrl'])
        product['detailImageUrls'] = detail_info['detailImageUrls']
        product['manufacturer'] = detail_info['manufacturer']
        product['country0f0rigin'] = detail_info['country0f0rigin']
        
        if detail_info['detailImageUrls']:
            product_dir = os.path.join('product_images', f"product_{idx}")
            if not os.path.exists(product_dir):
                os.makedirs(product_dir)
            
            for img_idx, img_url in enumerate(detail_info['detailImageUrls'], 1):
                img_filename = f"detail_{img_idx}.jpg"
                img_path = os.path.join(product_dir, img_filename)
                if download_image(img_url, img_path):
                    print(f"이미지 저장 완료: {img_filename}")
                time.sleep(0.5)
    
    # 진행 상황 업데이트
    crawled_data['last_product_index'] = idx
    if idx % 10 == 0:  # 10개 상품마다 진행 상황 저장
        save_progress()
    
    return product

def load_progress():
    """이전 크롤링 진행 상황 로드"""
    global crawled_data
    if os.path.exists('oliveyoung_products.json'):
        try:
            with open('oliveyoung_products.json', 'r', encoding='utf-8') as f:
                crawled_data = json.load(f)
            print(f"이전 진행 상황 로드 완료 (페이지: {crawled_data['last_page']}, 상품: {crawled_data['last_product_index']})")
            return True
        except:
            print("이전 진행 상황 로드 실패")
    return False

def main():
    base_url = "https://www.oliveyoung.co.kr/store/display/getMCategoryList.do?dispCatNo=1000001001100060001&fltDispCatNo=&prdSort=01&pageIdx=1&rowsPerPage=48&searchTypeSort=btn_thumb&plusButtonFlag=N&isLoginCnt=0&aShowCnt=0&bShowCnt=0&cShowCnt=0&trackingCd=Cat1000001001100060001_Small&amplitudePageGubun=&t_page=%EC%B9%B4%ED%85%8C%EA%B3%A0%EB%A6%AC%EA%B4%80&t_click=%EC%B9%B4%ED%85%8C%EA%B3%A0%EB%A6%AC%EC%83%81%EC%84%B8_%EC%86%8C%EC%B9%B4%ED%85%8C%EA%B3%A0%EB%A6%AC&midCategory=%EC%84%A0%ED%81%AC%EB%A6%BC&smallCategory=%EC%A0%84%EC%B2%B4&checkBrnds=&lastChkBrnd=&t_1st_category_type=%EB%8C%80_%EC%84%A0%EC%BC%80%EC%96%B4&t_2nd_category_type=%EC%A4%91_%EC%84%A0%ED%81%AC%EB%A6%BC&t_3rd_category_type=%EC%86%8C_%EC%84%A0%ED%81%AC%EB%A6%BC"
    # 한 페이지만 크롤링
    products = get_product_list(base_url)
    all_products = []
    for product in products:
        detail_info = get_product_detail_info(product['detailUrl']) if product['detailUrl'] else {'detailImageUrls': [], 'manufacturer': None, 'country0f0rigin': None}
        all_products.append({
            'productName': product['name'],
            'price': product['price'],
            'thumbnailUrls': product['thumbnailUrls'],
            'detailImageUrls': detail_info['detailImageUrls'],
            'manufacturer': detail_info['manufacturer'],
            'country0f0rigin': detail_info['country0f0rigin'],
            'productUrl': product['detailUrl']
        })
    crawled_data['items'] = all_products
    save_progress()
    print("\n크롤링이 완료되었습니다.")

if __name__ == "__main__":
    main() 