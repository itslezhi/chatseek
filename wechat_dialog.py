"""Local question/answer history, scoped to a single WeChat index."""
import json
import uuid


class DialogMemory:
    def __init__(self, store):
        self.store = store
        self.db = store.db
        with self.db:
            if not self.db.in_transaction:
                self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('''CREATE TABLE IF NOT EXISTS qa_turns(
                id INTEGER PRIMARY KEY, session TEXT NOT NULL,
                question TEXT NOT NULL, result TEXT NOT NULL
            )''')
            self.db.execute('CREATE INDEX IF NOT EXISTS qa_by_session ON qa_turns(session,id)')
            columns = {row['name'] for row in self.db.execute('PRAGMA table_info(qa_turns)')}
            if 'status' not in columns:
                self.db.execute("ALTER TABLE qa_turns ADD COLUMN status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived','deleted'))")
            if 'previous_status' not in columns:
                self.db.execute("ALTER TABLE qa_turns ADD COLUMN previous_status TEXT NOT NULL DEFAULT 'active'")
            self.db.execute('CREATE INDEX IF NOT EXISTS qa_by_status ON qa_turns(session,status,id)')


    def session(self):
        # A concurrent browser may have initialized this index already.
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO metadata(key,value) VALUES(?,?)',
                            ('dialog_session', json.dumps(uuid.uuid4().hex)))
        return self.store.meta('dialog_session')

    def check_session(self, expected=None):
        current = self.session()
        if expected is not None and expected != current:
            raise ValueError('已开始新对话，请刷新后重试。')
        return current

    def recent(self, session, limit=50, *, status='active'):
        rows = self.db.execute('SELECT id,question,result FROM qa_turns WHERE session=? AND status=? ORDER BY id DESC LIMIT ?',
                               (session, status, limit)).fetchall()
        return [{'turn_id':row['id'], 'question':row['question'], **json.loads(row['result'])}
                for row in reversed(rows)]

    def latest_id(self, session):
        return self.db.execute('SELECT coalesce(max(id),0) FROM qa_turns WHERE session=?',(session,)).fetchone()[0]

    def revision(self):
        return self.store.meta('dialog_revision',0)

    def manage(self, session, turn_id, action):
        if type(turn_id) is not int or turn_id<1 or action not in ('archive','unarchive','delete','restore'):
            raise ValueError('请选择有效的提问记录和操作。')
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            if self.store.meta('dialog_session')!=session:
                raise ValueError('对话已变更，请刷新后重试。')
            row=self.db.execute('SELECT status,previous_status FROM qa_turns WHERE session=? AND id=?',(session,turn_id)).fetchone()
            if row is None:
                raise ValueError('当前会话中没有这条提问记录。')
            status=row['status']
            if action=='archive' and status=='active':
                status='archived'
            elif action=='unarchive' and status=='archived':
                status='active'
            elif action=='delete' and status!='deleted':
                self.db.execute('UPDATE qa_turns SET previous_status=status WHERE session=? AND id=?',(session,turn_id))
                status='deleted'
            elif action=='restore' and status=='deleted':
                status=row['previous_status'] if row['previous_status'] in ('active','archived') else 'active'
            else:
                raise ValueError('记录状态已改变，请刷新后重试。')
            self.db.execute('UPDATE qa_turns SET status=? WHERE session=? AND id=?',(status,session,turn_id))
            self.store.set_meta('dialog_revision',self.revision()+1)
        return status

    def new(self, expected):
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            if self.store.meta('dialog_session') != expected:
                raise ValueError('已开始新对话，请刷新后重试。')
            session = uuid.uuid4().hex
            self.store.set_meta('dialog_session', session)
        return session

    def append(self, session, previous_id, question, result, *, expected_revision=None):
        # Avoid merging two simultaneous requests or an answer to a reset dialog.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.store.meta('dialog_session') != session:
                raise RuntimeError('已开始新对话，本次旧回答未加入新对话，请重新提问。')
            latest = self.latest_id(session)
            if latest != previous_id or (expected_revision is not None and self.revision()!=expected_revision):
                raise RuntimeError('另一窗口已更新对话，请刷新后重新提问。')
            cursor=self.db.execute('INSERT INTO qa_turns(session,question,result) VALUES(?,?,?)',
                                   (session, question, json.dumps(result, ensure_ascii=False)))
            self.store.set_meta('dialog_revision',self.revision()+1)
            self.db.commit()
            return cursor.lastrowid
        except Exception:
            self.db.rollback()
            raise


def model_history(turns):
    """Bound cloud context by both complete turns and character count."""
    selected, size = [], 2  # JSON array brackets and separators count too.
    for turn in reversed(turns[-6:]):
        item = {'question':turn['question'], 'answer':turn['answer'],
                'source_ids':[ref['id'] for ref in turn.get('sources', [])],
                'query_context':turn.get('query_context', {})}
        length = len(json.dumps(item, ensure_ascii=False)) + (2 if selected else 0)
        if size + length > 24000:
            break
        selected.append(item)
        size += length
    return list(reversed(selected))
