#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kindle-Dash 静态网站生成器
=========================
核心构建脚本，聚合多源数据并生成两套独立的 HTML 页面：
- dist/kindle.html: Kindle Voyage 离线看板
- dist/index.html: PC 桌面信息仪表盘

运行环境: Python 3.12 (Conda: dashboard-dev)
"""

import json
import os
import re
import glob
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import arxiv
import requests
from jinja2 import Environment, FileSystemLoader
import eng_to_ipa as ipa
from deep_translator import GoogleTranslator

# ==================== 配置常量 ====================

# 项目根目录
BASE_DIR = Path(__file__).parent.resolve()

# 数据目录
DATA_DIR = BASE_DIR / "data"
LYRICS_DIR = DATA_DIR / "lyrics"
MODELS_FILE = DATA_DIR / "models.json"
QUOTES_FILE = DATA_DIR / "quotes.json"
WORDS_FILE = DATA_DIR / "words.json"

# 模板和静态资源目录
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# 输出目录
DIST_DIR = BASE_DIR / "dist"

# 网络请求超时时间（秒）
REQUEST_TIMEOUT = 30


# ==================== Module A: Deep Tech (ArXiv 论文) ====================

def add_ipa_to_word(word: str) -> str:
    """
    为长度 > 7 的英文单词添加 IPA 音标上标。
    
    Args:
        word: 英文单词
        
    Returns:
        带音标标注的 HTML 字符串
    """
    # 只处理纯字母单词
    clean_word = re.sub(r'[^a-zA-Z]', '', word)
    if len(clean_word) <= 7:
        return word
    
    try:
        phonetic = ipa.convert(clean_word.lower())
        if phonetic and phonetic != clean_word.lower():
            return f"{word}<sup class='ipa'>/{phonetic}/</sup>"
    except Exception:
        pass
    
    return word


def add_ipa_to_text(text: str) -> str:
    """
    为文本中所有长度 > 7 的单词添加 IPA 音标。
    
    Args:
        text: 英文文本
        
    Returns:
        带音标标注的 HTML 字符串
    """
    words = text.split()
    return ' '.join(add_ipa_to_word(w) for w in words)


def translate_text(text: str, source: str = 'en', target: str = 'zh-CN') -> str:
    """
    使用 Google 翻译将文本翻译为目标语言。
    
    Args:
        text: 待翻译文本
        source: 源语言代码
        target: 目标语言代码
        
    Returns:
        翻译后的文本，失败时返回空字符串
    """
    if not text or not text.strip():
        return ""
    
    try:
        translator = GoogleTranslator(source=source, target=target)
        # 处理长文本：分段翻译
        if len(text) > 4500:
            # 按句号分割
            sentences = text.replace('. ', '.|').split('|')
            translated_parts = []
            current_chunk = ""
            
            for sentence in sentences:
                if len(current_chunk) + len(sentence) < 4500:
                    current_chunk += sentence
                else:
                    if current_chunk:
                        translated_parts.append(translator.translate(current_chunk))
                    current_chunk = sentence
            
            if current_chunk:
                translated_parts.append(translator.translate(current_chunk))
            
            return ' '.join(translated_parts)
        else:
            return translator.translate(text)
    except Exception as e:
        print(f"[警告] 翻译失败: {e}")
        return ""


def fetch_arxiv_papers(categories: list = None, max_results: int = 3) -> list:
    """
    从 ArXiv 获取最新论文。
    
    Args:
        categories: 论文类别列表，默认为 ['cs.AI', 'cs.CL']
        max_results: 获取论文数量
        
    Returns:
        论文信息列表
    """
    if categories is None:
        categories = ['cs.AI', 'cs.CL']
    
    papers = []
    query = ' OR '.join([f'cat:{cat}' for cat in categories])
    
    try:
        print(f"[Module A] 正在从 ArXiv 获取 {max_results} 篇最新论文...")
        
        client = arxiv.Client()
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending
        )
        
        for result in client.results(search):
            # 获取原始标题和摘要
            title_en = result.title
            abstract_en = result.summary.replace('\n', ' ')
            
            # 为标题添加音标
            title_with_ipa = add_ipa_to_text(title_en)
            
            # 翻译摘要
            abstract_zh = translate_text(abstract_en)
            
            papers.append({
                'title_en': title_en,
                'title_with_ipa': title_with_ipa,
                'abstract_en': abstract_en,
                'abstract_zh': abstract_zh,
                'authors': [author.name for author in result.authors[:3]],  # 最多3位作者
                'published': result.published.strftime('%Y-%m-%d'),
                'url': result.entry_id,
                'categories': result.categories
            })
            
        print(f"[Module A] 成功获取 {len(papers)} 篇论文")
        
    except Exception as e:
        print(f"[Module A] ArXiv 请求失败: {e}")
    
    return papers


# ==================== Module B: GitHub Trending AI ====================

def fetch_github_trending(days: int = 7, topic: str = 'artificial-intelligence', max_results: int = 10) -> list:
    """
    从 GitHub 搜索最近创建的 AI 相关热门仓库。
    
    Args:
        days: 过去多少天内创建的仓库
        topic: 主题标签
        max_results: 获取仓库数量
        
    Returns:
        仓库信息列表
    """
    repos = []
    
    try:
        # 计算日期范围
        since_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        # GitHub Search API
        url = "https://api.github.com/search/repositories"
        params = {
            'q': f'topic:{topic} created:>{since_date}',
            'sort': 'stars',
            'order': 'desc',
            'per_page': max_results
        }
        headers = {
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'Kindle-Dash-Builder'
        }
        
        print(f"[Module B] 正在搜索 GitHub 过去 {days} 天的 AI 热门仓库...")
        
        response = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        
        data = response.json()
        
        for item in data.get('items', [])[:max_results]:
            description_en = item.get('description', '') or ''
            description_zh = translate_text(description_en) if description_en else ''
            
            repos.append({
                'name': item['full_name'],
                'stars': item['stargazers_count'],
                'description_en': description_en,
                'description_zh': description_zh,
                'url': item['html_url'],
                'language': item.get('language', 'Unknown'),
                'created_at': item['created_at'][:10]
            })
        
        print(f"[Module B] 成功获取 {len(repos)} 个仓库")
        
    except requests.exceptions.Timeout:
        print(f"[Module B] GitHub 请求超时")
    except requests.exceptions.RequestException as e:
        print(f"[Module B] GitHub 请求失败: {e}")
    except Exception as e:
        print(f"[Module B] 处理 GitHub 数据失败: {e}")
    
    return repos


# ==================== Module C: Mental Models (思维模型) ====================

def load_mental_models() -> list:
    """
    从 JSON 文件加载思维模型数据。
    
    Returns:
        思维模型列表
    """
    models = []
    
    try:
        if MODELS_FILE.exists():
            with open(MODELS_FILE, 'r', encoding='utf-8') as f:
                models = json.load(f)
            print(f"[Module C] 成功加载 {len(models)} 个思维模型")
        else:
            print(f"[Module C] 思维模型文件不存在: {MODELS_FILE}")
    except json.JSONDecodeError as e:
        print(f"[Module C] JSON 解析失败: {e}")
    except Exception as e:
        print(f"[Module C] 加载思维模型失败: {e}")
    
    return models


# ==================== Module D: Focus & Quotes (专注与语录) ====================

def detect_language(text: str) -> str:
    """
    简单检测文本语言（中文/英文）。
    
    Args:
        text: 待检测文本
        
    Returns:
        'zh' 或 'en'
    """
    # 统计中文字符数量
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    total_chars = len(text.replace(' ', ''))
    
    if total_chars == 0:
        return 'en'
    
    # 如果中文字符超过 30%，认为是中文
    if chinese_chars / total_chars > 0.3:
        return 'zh'
    return 'en'


def load_quotes() -> list:
    """
    从 JSON 文件加载语录数据，并对英文语录进行翻译。
    
    Returns:
        语录列表（包含翻译）
    """
    quotes = []
    
    try:
        if QUOTES_FILE.exists():
            with open(QUOTES_FILE, 'r', encoding='utf-8') as f:
                raw_quotes = json.load(f)
            
            for quote in raw_quotes:
                content = quote.get('content', '')
                language = quote.get('language', detect_language(content))
                
                # 如果是英文，翻译为中文
                if language == 'en':
                    translation = translate_text(content)
                else:
                    translation = ''
                
                quotes.append({
                    'content': content,
                    'author': quote.get('author', '佚名'),
                    'language': language,
                    'translation': translation
                })
            
            print(f"[Module D] 成功加载 {len(quotes)} 条语录")
        else:
            print(f"[Module D] 语录文件不存在: {QUOTES_FILE}")
    except json.JSONDecodeError as e:
        print(f"[Module D] JSON 解析失败: {e}")
    except Exception as e:
        print(f"[Module D] 加载语录失败: {e}")
    
    return quotes


# ==================== Module E: Lyrics (歌词本) ====================

def load_lyrics() -> list:
    """
    扫描歌词目录，加载所有 .txt 歌词文件。
    
    Returns:
        歌词列表，每项包含歌名和内容
    """
    lyrics = []
    
    try:
        if LYRICS_DIR.exists():
            txt_files = sorted(LYRICS_DIR.glob('*.txt'))
            
            for txt_file in txt_files:
                song_name = txt_file.stem  # 文件名作为歌名
                
                with open(txt_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                lyrics.append({
                    'name': song_name,
                    'content': content,
                    'lines': content.split('\n')
                })
            
            print(f"[Module E] 成功加载 {len(lyrics)} 首歌词")
        else:
            print(f"[Module E] 歌词目录不存在: {LYRICS_DIR}")
    except Exception as e:
        print(f"[Module E] 加载歌词失败: {e}")
    
    return lyrics


# ==================== Module F: Words (单词记忆) ====================

WORDS_FILE = DATA_DIR / "words.json"

def load_words() -> list:
    """
    从 JSON 文件加载计算机专业英语单词数据。
    
    Returns:
        单词列表，每项包含单词、音标、释义和例句
    """
    words = []
    
    try:
        words_file = DATA_DIR / "words.json"
        if words_file.exists():
            with open(words_file, 'r', encoding='utf-8') as f:
                words = json.load(f)
            print(f"[Module F] 成功加载 {len(words)} 个单词")
        else:
            print(f"[Module F] 单词文件不存在: {words_file}")
    except json.JSONDecodeError as e:
        print(f"[Module F] JSON 解析失败: {e}")
    except Exception as e:
        print(f"[Module F] 加载单词失败: {e}")
    
    return words


# ==================== 模板渲染与生成 ====================

def render_templates(context: dict) -> None:
    """
    使用 Jinja2 渲染 HTML 模板并输出到 dist 目录。
    
    Args:
        context: 模板上下文数据
    """
    # 确保输出目录存在
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    
    # 初始化 Jinja2 环境
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True
    )
    
    # 添加自定义模板变量
    context['generated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 读取并内联 CSS
    kindle_css_path = STATIC_DIR / 'style_kindle.css'
    web_css_path = STATIC_DIR / 'style_web.css'
    
    context['kindle_css'] = kindle_css_path.read_text(encoding='utf-8') if kindle_css_path.exists() else ''
    context['web_css'] = web_css_path.read_text(encoding='utf-8') if web_css_path.exists() else ''
    
    # 渲染 Kindle 版本
    try:
        kindle_template = env.get_template('kindle.html')
        kindle_html = kindle_template.render(**context)
        
        kindle_output = DIST_DIR / 'kindle.html'
        kindle_output.write_text(kindle_html, encoding='utf-8')
        print(f"[生成] {kindle_output}")
    except Exception as e:
        print(f"[错误] 渲染 Kindle 模板失败: {e}")
    
    # 渲染 PC Web 版本
    try:
        web_template = env.get_template('web.html')
        web_html = web_template.render(**context)
        
        web_output = DIST_DIR / 'index.html'
        web_output.write_text(web_html, encoding='utf-8')
        print(f"[生成] {web_output}")
    except Exception as e:
        print(f"[错误] 渲染 Web 模板失败: {e}")


# ==================== 主函数 ====================

def main():
    """
    主函数：聚合所有数据源并生成 HTML 文件。
    """
    print("=" * 60)
    print("Kindle-Dash 静态网站生成器")
    print("=" * 60)
    print()
    
    # 收集所有模块数据
    context = {
        # Module A: Deep Tech (ArXiv 论文)
        'papers': fetch_arxiv_papers(),
        
        # Module B: GitHub Trending AI
        'repos': fetch_github_trending(),
        
        # Module C: Mental Models
        'models': load_mental_models(),
        
        # Module D: Focus & Quotes
        'quotes': load_quotes(),
        
        # Module E: Lyrics
        'lyrics': load_lyrics(),
        
        # Module F: Words (单词记忆)
        'words': load_words(),
    }
    
    print()
    print("-" * 60)
    print()
    
    # 渲染模板
    render_templates(context)
    
    print()
    print("=" * 60)
    print("生成完成！")
    print(f"- Kindle 版本: {DIST_DIR / 'kindle.html'}")
    print(f"- PC Web 版本: {DIST_DIR / 'index.html'}")
    print("=" * 60)


if __name__ == '__main__':
    main()
