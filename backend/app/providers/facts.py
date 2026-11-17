"""Conservative source-side fact inventory, independent of a model's self-report.

This is deliberately not a general named-entity recognizer. Explicitly recognized
facts are immutable; uncertain fact-bearing sentences are kept verbatim and shown
for human review. Plain descriptive sentences can still be polished.
"""

import re
from dataclasses import dataclass

TIME_PATTERN = re.compile(
    r"(?:\d{4}[年/-]\d{1,2}[月/-]\d{1,2}[日号]?|\d{1,2}月\d{1,2}[日号])"
    r"(?:\s*(?:上午|下午|晚上|中午|早上)?\s*\d{1,2}(?:[:：]\d{2}|点(?:半|\d{1,2}分)?))?"
    r"|(?:今天|明天|后天|本周|下周|周[一二三四五六日天]|星期[一二三四五六日天])"
    r"(?:\s*(?:上午|下午|晚上|中午|早上)?\s*\d{1,2}(?:[:：]\d{2}|点(?:半|\d{1,2}分)?))?"
    r"|(?<!\d)\d{1,2}[:：]\d{2}(?!\d)"
)
PERSON_PATTERN = re.compile(
    r"(?:特邀嘉宾|受邀嘉宾|嘉宾|主讲人|主讲|报告人|演讲者|讲师|讲者|联系人|负责人|主持人|姓名|作者)"
    r"\s*[：:]?\s*"
    r"(?P<value>[\u4e00-\u9fff]{2,4}?|[A-Z][A-Za-z'-]+(?: [A-Z][A-Za-z'-]+){0,3})"
    r"(?=将|于|在|会|来|介绍|分享|演讲|授课|参与|出席|老师|教授|博士|先生|女士|同学|[，。；！？，、\s]|$)"
)
SURNAME_PATTERN = re.compile(
    r"(?:^|[，。；！？\s]|邀请|欢迎|由|与|和|请)"
    r"(?P<value>(?:欧阳|司马|上官|诸葛|东方|夏侯|皇甫|赵|钱|孙|李|周|吴|郑|王|冯|陈|褚|卫|蒋|沈|韩|杨|朱|秦|尤|许|何|吕|施|张|孔|曹|严|华|金|魏|陶|姜|谢|邹|喻|柏|水|窦|章|云|苏|潘|葛|奚|范|彭|郎|鲁|韦|昌|马|苗|凤|花|方|俞|任|袁|柳|唐|罗|薛|伍|余|米|贝|姚|孟|顾|尹|江|钟|熊|杜|贾|莫|廖|萧|肖|高|刘|林|郭|胡|徐|曾|程|丁|宋|黄|谭|邓|傅|梁|汪|白|田|石|邱|龚|崔|龙|段|雷|武|阎|覃|黎|易|常|文|乔|毛|史|戴|夏|邵|于|侯)"
    r"[\u4e00-\u9fff]{1,2}?)"
    r"(?=将|于|在|来到|表示|分享|演讲|授课|主持|老师|教授|博士|同学|先生|女士|[，。；！？\s]|$)"
)
LOCATION_PATTERN = re.compile(
    r"(?:活动地点|集合地点|地点|地址|会场|场地)\s*[：:]\s*(?P<label>[^，。；！？\n]{1,80})"
    r"|(?:位于|前往|来到|在|于|到)\s*(?P<context>(?:(?!在|于|\d{1,2}月|\d{4}年)[\u4e00-\u9fffA-Za-z0-9·#（）()\-]){0,30}?"
    r"(?:图书馆|教学楼|报告厅|会议室|体育馆|体育场|礼堂|广场|教室|实验室|活动中心|交流区|校区|公园|大厦|会堂)"
    r"(?:[一二三四五六七八九十\d]+(?:楼|层|号|室)|[东西南北]区|[A-Za-z]?\d{2,4}室){0,3})"
)
# These cues are deliberately broad. Unknown identities/addresses are reviewed as
# a sentence rather than guessed as a name or silently entrusted to the model.
UNCERTAIN_CUE = re.compile(
    r"嘉宾|主讲|讲师|讲者|老师|教授|博士|先生|女士|负责人|主持人|联系人|姓名|"
    r"将于|将到|将赴|位于|地址|会场|活动地点|集合地点|(?:在|于).{1,35}(?:举行|召开|交流|授课|演讲)"
)
PERSON_CUE = re.compile(r"嘉宾|主讲|讲师|讲者|老师|教授|博士|先生|女士|负责人|主持人|联系人|姓名")
UNKNOWN_ACTOR = re.compile(r"(?P<actor>[\u4e00-\u9fff]{2,6})(?=将于|将到|将赴|来到|表示|担任)")
GENERIC_ACTOR = re.compile(
    r"活动$|会议$|讲座$|课程$|比赛$|演出$|读书会$|分享会$|我们$|大家$|通知$|原文$|时间$"
)
LOCATION_CUE = re.compile(r"位于|地址|住址|会场|地点|图书馆|教学楼|会议室|报告厅|校区|礼堂")
SENTENCE_PATTERN = re.compile(r"[^。！？\n]+[。！？]?", re.UNICODE)


@dataclass(frozen=True)
class SourceFact:
    kind: str
    value: str


def entity_tokens(text: str) -> list[SourceFact]:
    tokens = [SourceFact("time", match.group()) for match in TIME_PATTERN.finditer(text)]
    for pattern in (PERSON_PATTERN, SURNAME_PATTERN):
        tokens.extend(SourceFact("person", match["value"]) for match in pattern.finditer(text))
    tokens.extend(
        SourceFact("location", match["label"] or match["context"])
        for match in LOCATION_PATTERN.finditer(text)
    )
    return list(dict.fromkeys(tokens))


def uncertain_sentences(text: str) -> list[str]:
    """Keep a whole uncertain clause only when regex cannot identify its facts."""
    result = []
    for match in SENTENCE_PATTERN.finditer(text):
        sentence = match.group().strip()
        kinds = {token.kind for token in entity_tokens(sentence)}
        actor_cue = any(
            not GENERIC_ACTOR.search(match["actor"]) for match in UNKNOWN_ACTOR.finditer(sentence)
        )
        unknown_person = (PERSON_CUE.search(sentence) or actor_cue) and "person" not in kinds
        unknown_location = LOCATION_CUE.search(sentence) and "location" not in kinds
        unknown_context = UNCERTAIN_CUE.search(sentence) and not kinds
        if sentence and (unknown_person or unknown_location or unknown_context):
            result.append(sentence)
    return result


def uncertain_kind(sentence: str) -> str:
    if PERSON_CUE.search(sentence):
        return "person"
    if LOCATION_CUE.search(sentence):
        return "location"
    return "time"


def infer_intent(raw: str) -> str:
    """Documented keyword routing for mock mode, not semantic AI understanding."""
    if re.search(r"回顾|纪实|记录|总结|活动回放", raw):
        return "record"
    if re.search(r"通知|公告|启事|须知|声明|温馨提示", raw):
        return "announcement"
    if re.search(r"活动|讲座|读书会|分享会|报名|邀请|招募|比赛|演出|音乐会|时间\s*[：:]", raw):
        return "event"
    return "announcement"
