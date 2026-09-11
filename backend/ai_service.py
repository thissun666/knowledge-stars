# -*- coding: utf-8 -*-
"""glm-4-flash 客户端: OpenAI 兼容接口, 超时+有限重试+显式降级。

  - call_llm 支持单轮(prompt, system)与多轮(messages=[...])
  - 出题/批改/纠错/追问 prompt 注入前置掌握情况(差异化核心)
  - 讲解 prompt 要求开头自然衔接已掌握前置(图谱上下文肉眼可见)
"""
import logging
import re
import time
from typing import List, Optional, Tuple

import httpx

from backend import config

logger = logging.getLogger("app.ai")

TIMEOUT = 45.0
MAX_RETRY = 2

SUBJECT_CN = {"Mathematics": "数学", "English": "英语"}

_PLACEHOLDERS = {"", "略", "changeme", "todo",
                 "your-api-key-here", "yourapikey"}


class AIError(RuntimeError):
    """AI 调用失败(未配置/网络/响应异常), 由路由层转 code=6。"""


def is_configured() -> bool:
    key = (config.AI_API_KEY or "").strip().lower()
    if key.startswith("your-api-key"):
        return False
    return key not in _PLACEHOLDERS


def call_llm(content: str = "", system: str = "",
             messages: Optional[List[dict]] = None) -> str:
    if not is_configured():
        raise AIError("AI_API_KEY 未配置, 请在 .env 填入智谱 Key 后重启服务")
    if messages is None:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
    body = {"model": config.AI_MODEL, "messages": messages,
            "temperature": 0.6}
    headers = {"Authorization": "Bearer " + config.AI_API_KEY.strip()}
    url = config.AI_BASE_URL.rstrip("/") + "/chat/completions"
    last: Optional[Exception] = None
    for attempt in range(MAX_RETRY + 1):
        try:
            resp = httpx.post(url, json=body, headers=headers,
                              timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if content and content.strip():
                return content.strip()
            raise ValueError("空响应")
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            last = exc
            logger.warning("LLM 调用失败(第 %d 次): %s", attempt + 1, exc)
            if attempt < MAX_RETRY:
                time.sleep(0.6 * (attempt + 1))
    raise AIError("AI 调用失败: " + str(last))


def _prereq_lines(prereq_status: List[Tuple[str, bool]]) -> str:
    if not prereq_status:
        return "无(根知识点)"
    return "、".join(
        n + "(" + ("已掌握" if ok else "未掌握") + ")"
        for n, ok in prereq_status)


def build_explain_prompt(name: str, subject: str, domain: str,
                         grade: int, description: str,
                         prereq_names: List[str]) -> str:
    subj = SUBJECT_CN.get(subject, subject)
    g = grade if grade > 0 else 6
    pre = "、".join(prereq_names) if prereq_names else "无(根知识点)"
    return (
        "请为学生讲解知识点, 用简体中文。\n"
        "学科: " + subj + "\n年级: " + str(g) + "\n领域: " + domain + "\n"
        "知识点: " + name + "\n内容描述: " + (description or "无") +
        "\n前置知识: " + pre + "\n"
        "要求: 如果前置知识里标注了学生已掌握的内容, 开头先用一句话"
        "自然衔接它(例如 借助你学过的xx), 再进入正文。\n"
        "输出 Markdown, 依次包含四个小节(用 ## 开头):\n"
        "## 是什么\n## 生活中的例子\n## 常见误区\n## 小练习\n"
        "小练习只出 1 道并附答案与解析。"
        "总字数不超过 400 字, 语言贴近 " + str(g) + " 年级学生, 多用比喻。")


def build_translate_prompt(name: str, domain: str,
                           description: str) -> str:
    return (
        "将下面的中小学知识点信息翻译成简体中文。\n"
        "输出格式严格为两行, 不要输出任何其他内容:\n"
        "名称: <知识点中文名, 简洁准确, 不超过 15 个字>\n"
        "描述: <描述译文, 通俗易懂>\n\n"
        "知识点名: " + name + "\n领域: " + domain +
        "\n描述: " + (description or "无"))


def build_mistake_prompt(name: str, subject: str, domain: str, grade: int,
                         description: str,
                         prereq_status: List[Tuple[str, bool]],
                         question: str, my_answer: str) -> str:
    subj = SUBJECT_CN.get(subject, subject)
    g = grade if grade > 0 else 6
    return (
        "学生做错了与知识点相关的题, 请针对性纠错, 用简体中文。\n"
        "知识点: " + name + "(学科:" + subj + ", 年级" + str(g) +
        ", 领域:" + domain + ")\n"
        "内容描述: " + (description or "无") + "\n"
        "前置知识掌握情况: " + _prereq_lines(prereq_status) + "\n\n"
        "题目: " + question + "\n学生的答案/做法: " + my_answer + "\n\n"
        "输出 Markdown, 依次四个小节(用 ## 开头):\n"
        "## 错在哪一步\n## 正确思路\n## 需要回去补的基础\n"
        "## 巩固练习(1道, 附答案)\n"
        "要求: 已掌握的前置不要重讲; 未掌握的前置要明确指出要回去补; "
        "总字数300以内, 贴合" + str(g) + "年级认知。")


def build_ask_system(name: str, subject: str, domain: str, grade: int,
                     prereq_status: List[Tuple[str, bool]]) -> str:
    subj = SUBJECT_CN.get(subject, subject)
    g = grade if grade > 0 else 6
    return (
        "你是一名耐心的中小学一对一辅导老师。学生正在学习《" + name + "》"
        "(学科:" + subj + ", 年级" + str(g) + ", 领域:" + domain + ")。"
        "学生已看过该知识点的讲解, 现在就同一知识点继续追问。"
        "前置知识掌握情况: " + _prereq_lines(prereq_status) + "。"
        "回答规则: 简体中文; 先直接回答再简短解释; 贴合" + str(g) +
        "年级认知, 多用比喻; 不确定时诚实说明; 单次回答200字以内。")


def build_practice_prompt(name: str, subject: str, domain: str,
                          grade: int, description: str,
                          prereq_names: List[str]) -> str:
    subj = SUBJECT_CN.get(subject, subject)
    g = grade if grade > 0 else 6
    pre = "、".join(prereq_names) if prereq_names else "无(根知识点)"
    return (
        "为下面的知识点出 1 道练习题, 用简体中文。\n"
        "学科: " + subj + "\n年级: " + str(g) + "\n领域: " + domain + "\n"
        "知识点: " + name + "\n内容描述: " + (description or "无") +
        "\n前置知识: " + pre + "\n\n"
        "要求:\n"
        "1. 只输出题目本身, 不要输出答案、解析或任何提示\n"
        "2. 贴合" + str(g) + "年级认知, 尽量结合生活场景\n"
        "3. 答案应简短(一个数/一个词/一句话), 便于学生键盘输入\n"
        "4. 直接输出题目, 不要加 题目: 之类的前缀")


def build_grade_prompt(name: str, subject: str, domain: str, grade: int,
                       description: str,
                       prereq_status: List[Tuple[str, bool]],
                       question: str, student_answer: str) -> str:
    subj = SUBJECT_CN.get(subject, subject)
    g = grade if grade > 0 else 6
    return (
        "批改学生的作答, 用简体中文。\n"
        "知识点: " + name + "(学科:" + subj + ", 年级" + str(g) +
        ", 领域:" + domain + ")\n"
        "内容描述: " + (description or "无") + "\n"
        "前置知识掌握情况: " + _prereq_lines(prereq_status) + "\n\n"
        "题目: " + question + "\n学生的作答: " + student_answer + "\n\n"
        "先自己解题, 再对照学生作答判分。输出格式严格为:\n"
        "判定: 正确 或 判定: 错误\n"
        "正确答案: <正确答案>\n"
        "解析: <判分理由; 若错误, 指出错在哪一步、建议回去补什么, 200字以内>")


def parse_judgement(text: str) -> Optional[bool]:
    m = re.search(r"判定[:：]\s*(正确|错误)", text)
    if m:
        return m.group(1) == "正确"
    return None


def parse_translate(text: str,
                    fallback_name: str,
                    fallback_desc: str) -> Tuple[str, str]:
    name, desc = fallback_name, fallback_desc
    m1 = re.search(r"名称[:：]\s*(.+)", text)
    if m1:
        name = m1.group(1).strip() or name
    m2 = re.search(r"描述[:：]\s*(.+)", text, re.S)
    if m2:
        desc = m2.group(1).strip() or desc
    elif text.strip():
        desc = text.strip()
    return name, desc
