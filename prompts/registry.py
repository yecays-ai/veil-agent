"""Ticket 400: the one list of text that can reach the model.

Every entry here is visible in the instructions page. Kind decides how it is shown and stored:

- text   default text in prompts/defaults/<id>.md; Lan's override in persona/instructions/<id>.md
- marker a block the program fills in at request time; visible, never editable

Persona, background, talking samples and his notes are NOT registered here: each already has its own
page. Two editable copies of the same text is how versions get confused (Lan, 2026-11-20).

An entry with editable=False is still listed with its full text; it is only refused as a target of
edits (protocol or safety text the program depends on).
"""

CATEGORIES = (
    ('identity', '人格与身份'),
    ('voice', '说话方式'),
    ('background', '背景资料'),
    ('memory', '记忆'),
    ('autonomy', '自主活动'),
    ('tools', '工具'),
    ('system', '系统与安全'),
    ('runtime', '运行时'),
)

CATEGORY_IDS = tuple(item[0] for item in CATEGORIES)
CATEGORY_TITLES = dict(CATEGORIES)
KINDS = ('text', 'marker')


class Entry:
    __slots__ = ('id', 'category', 'title', 'kind', 'site', 'editable', 'max_bytes', 'note', 'file', 'marker')

    def __init__(self, id, category, title, kind, site, editable=True, max_bytes=8000,
                 note='', file='', marker=''):
        self.id = id
        self.category = category
        self.title = title
        self.kind = kind
        self.site = site
        self.editable = editable
        self.max_bytes = max_bytes
        self.note = note
        self.file = file
        self.marker = marker

    def as_dict(self):
        return {'id': self.id, 'category': self.category, 'title': self.title,
                'kind': self.kind, 'site': self.site, 'editable': self.editable,
                'max_bytes': self.max_bytes, 'note': self.note,
                'file': self.file, 'marker': self.marker}


def _text(id, category, title, site, *, editable=True, max_bytes=8000, note=''):
    return Entry(id, category, title, 'text', site, editable=editable,
                 max_bytes=max_bytes, note=note)


def _marker(id, category, title, site, marker):
    return Entry(id, category, title, 'marker', site, editable=False, marker=marker)


ENTRIES = (
    # ---------- 人格与身份 ----------
    # 人设、背景、说话样例、他记的你不在这一页：它们各有自己的页面，避免两处各改一份。
    _text('identity.user_frame', 'identity', '「关于你」的开头说明',
          '请求第 2 段，设定正文之前', max_bytes=1000,
          note='程序用这句话说明它是数据、不是命令。'),
    _text('system.facts', 'identity', '系统事实（身份、模型、能力）',
          '请求第 5 段；每轮都带，程序写明它压过人设与历史', max_bytes=6000,
          note='花括号里的内容由程序每轮填入；你可以改花括号之外的句子。'),
    _marker('system.capabilities_notice', 'system', '能力更新通知',
            '下一段对话的运行时追加，只出现一次', 'capability_event'),
    _marker('system.mcp_notice', 'system', 'MCP 故障说明',
            'MCP 服务不可用或刚恢复时追加', 'mcp_problem'),

    # ---------- 说话方式 ----------
    _text('voice.frame', 'voice', '说话样例的说明',
          '请求第 3 段，样例文件之前；样例关掉时整段不带', max_bytes=4000),
    _text('chat.send_message', 'tools', '发消息工具的写法要求',
          '工具定义里；每轮都带', max_bytes=3000,
          note='这是「一句一条」等说话习惯的来源。'),
    _text('chat.stay_silent', 'tools', '沉默工具的说明',
          '工具定义里；每轮都带', max_bytes=1500),
    _text('chat.reply_later', 'tools', '稍后回工具的说明',
          '工具定义里；每轮都带', max_bytes=1500),
    _text('chat.recall_message', 'tools', '撤回消息工具的说明',
          '工具定义里；每轮都带', max_bytes=2000),

    # ---------- 背景资料 ----------
    _text('background.note', 'background', '背景段落的开头说明',
          '背景段落的第一行', max_bytes=1500),
    _marker('background.selected', 'background', '本轮选中的背景段落',
            '历史之后；最多 3 段 / 1500 字', 'background'),

    # ---------- 记忆 ----------
    _text('memory.learned.header', 'memory', '记忆笔记的开头',
          '请求第 4 段；有内容才带', max_bytes=1500),
    _text('memory.learned.growth', 'memory', '「关系成长」小标题',
          '请求第 4 段内', max_bytes=800),
    _text('memory.learned.facts', 'memory', '「重要事实」小标题',
          '请求第 4 段内', max_bytes=800),
    _text('memory.ongoing.header', 'memory', '「正在进行」的开头',
          '请求靠后，用户消息之前；有进行中的事才带', max_bytes=1500),
    _text('memory.recall.head', 'memory', '自动想起记忆的开头',
          '历史之后、运行时限制之前', max_bytes=2000),
    _text('memory.recall.empty', 'memory', '没想起任何记忆时的说明',
          '同上，没有命中时替换条目', max_bytes=800),
    _text('memory.opening.head', 'memory', '开场记忆的开头',
          '新对话第一次请求', max_bytes=2000),
    _text('memory.opening.empty', 'memory', '没有开场记忆时的说明',
          '新对话第一次请求', max_bytes=800),
    _text('memory.experience.heading', 'memory', '最近几天经历笔记的开头',
          '请求靠前，聊天历史之前', max_bytes=3000),
    _marker('memory.recalled', 'memory', '自动想起的记忆条目',
            '历史之后；最多 3 条', 'recall'),
    _marker('memory.opening', 'memory', '开场记忆条目',
            '新对话第一次请求', 'opening'),
    _marker('memory.ongoing', 'memory', '正在进行的事',
            '用户消息之前', 'ongoing'),
    _marker('memory.recent_experiences', 'memory', '上次瞄一眼的记录',
            '运行时追加；下一次瞄一眼会替换', 'recent_experiences'),
    _marker('memory.pending_approvals', 'memory', '待批准的动作',
            '自主活动醒来提示里', 'pending_approvals'),

    # ---------- 自主活动 ----------
    _text('autonomy.wake.header', 'autonomy', '醒来提示的过渡句',
          '醒来提示（当轮用户消息）中段', max_bytes=1000),
    _text('autonomy.wake.rules', 'autonomy', '醒来提示的行为段',
          '醒来提示里「Time is yours…」那一段', max_bytes=8000,
          note='这一段可以随便改；它后面四行记录格式是程序加的，不在这条里。'),
    _text('autonomy.wake.protocol', 'autonomy', '醒来记录的四行格式',
          '醒来提示最末尾；程序靠它写活动记录', max_bytes=1200, editable=False,
          note='Decision / Reason / Focus / Next step 四行是程序读活动记录用的，改了会记不下这次活动；所以只读。'),
    _text('autonomy.threads.frame', 'autonomy', '跨唤醒线索的开头',
          '醒来提示里；[autonomy] threads 关闭时不带', max_bytes=1500),
    _text('autonomy.reads.frame', 'autonomy', '已读未变的开头',
          '醒来提示里；同上', max_bytes=1500),
    _text('autonomy.glance.rules', 'autonomy', '瞄一眼的说明',
          '瞄一眼请求的 system 上下文', max_bytes=3000),
    _text('autonomy.glance.note', 'autonomy', '瞄一眼的收尾要求',
          '瞄一眼请求的 system 上下文末尾', max_bytes=1500),
    _marker('autonomy.previous', 'autonomy', '上一次自主活动',
            '醒来提示里；程序记录的 JSON', 'autonomy_previous'),
    _marker('autonomy.earlier', 'autonomy', '更早的自主活动',
            '醒来提示里；最多再往前几次', 'autonomy_earlier'),
    _marker('autonomy.threads', 'autonomy', '跨唤醒线索的条目',
            '醒来提示里', 'threads'),
    _marker('autonomy.reads', 'autonomy', '已读未变的文件清单',
            '醒来提示里', 'reads'),
    _marker('autonomy.task', 'autonomy', '当前长期任务',
            '醒来提示里', 'long_task'),
    _marker('autonomy.life', 'autonomy', '生活记录与日程',
            '醒来提示里', 'life'),
    _marker('autonomy.lab', 'autonomy', 'Lab 最新文件',
            '醒来提示里', 'lab'),
    _marker('autonomy.thoughts', 'autonomy', '想法索引摘录',
            '醒来提示里；最多两条', 'thoughts'),
    _marker('autonomy.budget', 'autonomy', '今日预算',
            '醒来提示里', 'budget'),
    _marker('autonomy.missed_alarms', 'autonomy', '错过的闹钟',
            '醒来提示里；有才带', 'missed_alarms'),

    # ---------- 系统与安全 ----------
    _marker('runtime.time_ledger', 'runtime', '时间与花费账本',
            '每轮最后一条 system 消息；每轮都变', 'time_ledger'),
    _marker('runtime.history', 'runtime', '聊天历史',
            '人设之后、当轮消息之前；默认 80 轮', 'history'),
    _marker('runtime.time_separators', 'runtime', '历史里的时间分隔行',
            '超过一小时的两条消息之间', 'time_separator'),
    _marker('runtime.work_checkpoint', 'runtime', '工作断点',
            '历史之后；新对话或丢 20 轮时再带', 'work_checkpoint'),
    _marker('runtime.investigation_checkpoint', 'runtime', '调查断点',
            '历史之后；命中时带', 'investigation_checkpoint'),
    _marker('runtime.recall_candidates', 'runtime', '撤回候选',
            '历史之后；120 秒内他自己的消息', 'recall_candidates'),
    _marker('runtime.virtual_cards', 'runtime', '虚拟卡片',
            '历史之后；有待处理卡片时', 'virtual_cards'),
    _marker('runtime.screen_pop', 'runtime', '弹幕与屏幕消息记录',
            '历史之后；有记录时', 'screen_pop'),
    _marker('runtime.channel_marker', 'runtime', '渠道来源标记（QQ / 邮件）',
            '你的消息最前面', 'channel_marker'),
)

BY_ID = {entry.id: entry for entry in ENTRIES}


def entries():
    return list(ENTRIES)


def get(entry_id):
    try:
        return BY_ID[entry_id]
    except KeyError:
        raise KeyError('未登记的指令编号：' + str(entry_id)) from None


def by_category():
    result = []
    for category, title in CATEGORIES:
        result.append({'id': category, 'title': title,
                       'entries': [entry for entry in ENTRIES if entry.category == category]})
    return result


def validate(defaults_dir):
    """Every text entry must have a default file; ids and categories must be unique and valid."""
    from pathlib import Path
    seen = set()
    for entry in ENTRIES:
        if entry.id in seen:
            raise ValueError('重复的指令编号：' + entry.id)
        seen.add(entry.id)
        if entry.category not in CATEGORY_IDS:
            raise ValueError('未知分类：' + entry.id)
        if entry.kind not in KINDS:
            raise ValueError('未知类型：' + entry.id)
        if entry.kind == 'text':
            path = Path(defaults_dir) / (entry.id + '.md')
            if not path.is_file():
                raise ValueError('缺少默认文本：' + str(path))

    return True
