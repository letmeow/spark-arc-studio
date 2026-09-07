"""项目删除与后台任务生命周期回归。"""

from __future__ import annotations

import asyncio
import json
import threading

from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.chat_manager import ChatManager
from agents.routes import chat_task
from core.models import ChatMessage, ProjectVersion, Share, User, UserInfo


def test_delete_project_refuses_to_remove_directory_while_background_task_is_alive(
    monkeypatch,
    tmp_path,
) -> None:
    from story import routes_project

    project_path = tmp_path / "demo"
    project_path.mkdir()

    monkeypatch.setattr(routes_project, "get_project_path", lambda user_id, project_name: str(project_path))
    monkeypatch.setattr(
        routes_project,
        "_cancel_project_background_builds",
        lambda user_id, project_name: ["自动写作任务未在等待时间内停止"],
    )
    remove_calls: list[tuple] = []
    monkeypatch.setattr(
        routes_project,
        "_remove_project_directory_with_retries",
        lambda *args: remove_calls.append(args),
    )

    response = asyncio.run(routes_project.delete_project("demo", {"user_id": "7"}))

    assert isinstance(response, JSONResponse)
    assert response.status_code == 409
    assert json.loads(response.body)["details"] == ["自动写作任务未在等待时间内停止"]
    assert remove_calls == []
    assert project_path.is_dir()


def _isolated_user_db(monkeypatch) -> sessionmaker:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    UserInfo.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        session.add(User(id=7, username="lifecycle-user", password_hash="x", salt="x"))
        session.commit()
    monkeypatch.setattr("agents.chat_manager.UserInfoSession", session_factory)
    monkeypatch.setattr("story.routes_project.UserInfoSession", session_factory)
    return session_factory


def _register_task(*, user_id: str, project_name: str, agent_id: str, status: str, finished: bool):
    entry = chat_task.ChatTaskEntry(
        task_key=chat_task._make_task_key(user_id, project_name, agent_id, "global"),
        user_id=user_id,
        project_name=project_name,
        agent_id=agent_id,
        context_key="global",
        stop_event=threading.Event(),
        status=status,
        started_at=0.0,
    )
    if finished:
        entry.finished_event.set()
        entry.terminalized = True
        entry.terminal_status = status
    chat_task.register_task(entry)
    return entry


def test_delete_project_clears_chat_versions_shares_and_memory_tasks(monkeypatch, tmp_path) -> None:
    """删除项目后，同名重建不应继承旧聊天、旧版本/分享与旧内存任务。"""
    from story import routes_project

    session_factory = _isolated_user_db(monkeypatch)
    project_path = tmp_path / "demo"
    project_path.mkdir()
    version_snapshot = tmp_path / "ver_old.db"
    version_snapshot.write_text("version")
    share_snapshot = tmp_path / "share_old.db"
    share_snapshot.write_text("share")

    monkeypatch.setattr(routes_project, "get_project_path", lambda user_id, project_name: str(project_path))
    monkeypatch.setattr(routes_project, "_cancel_project_background_builds", lambda user_id, project_name: [])
    monkeypatch.setattr(
        routes_project,
        "_remove_project_directory_with_retries",
        lambda user_id, project_name, path: __import__("shutil").rmtree(path, ignore_errors=True),
    )

    ChatManager(user_id=7, project_name="demo").append_message(
        agent_id="agent_director", context_key="global", role="user", content="旧聊天"
    )
    with session_factory() as session:
        session.add(ProjectVersion(id="v-old", user_id=7, project_name="demo", version_name="v1", snapshot_path=str(version_snapshot)))
        session.add(Share(id="s-old", user_id=7, project_name="demo", title="旧分享", snapshot_path=str(share_snapshot)))
        session.commit()

    finished_entry = _register_task(user_id="7", project_name="demo", agent_id="agent_director", status="completed", finished=True)
    running_entry = _register_task(user_id="7", project_name="demo", agent_id="agent_lorebook", status="running", finished=False)
    other_entry = _register_task(user_id="7", project_name="other", agent_id="agent_director", status="running", finished=False)

    try:
        response = asyncio.run(routes_project.delete_project("demo", {"user_id": "7"}))

        assert response == {"success": True, "message": "项目删除成功"}
        assert running_entry.stop_event.is_set() is True
        assert running_entry.cancel_requested is True
        with session_factory() as session:
            assert session.execute(select(ChatMessage).where(ChatMessage.user_id == 7)).all() == []
            assert session.execute(select(ProjectVersion).where(ProjectVersion.user_id == 7)).all() == []
            assert session.execute(select(Share).where(Share.user_id == 7)).all() == []
        assert version_snapshot.exists() is False
        assert share_snapshot.exists() is False
        assert chat_task.list_recent_tasks("7", "demo") == []
        assert ChatManager(user_id=7, project_name="demo").get_history(agent_id="agent_director", context_key="global") == []
        assert chat_task.get_task(other_entry.task_key) is other_entry
        assert finished_entry.task_key not in chat_task._active_chat_tasks
    finally:
        chat_task._active_chat_tasks.pop(finished_entry.task_key, None)
        chat_task._active_chat_tasks.pop(running_entry.task_key, None)
        chat_task._active_chat_tasks.pop(other_entry.task_key, None)


def test_create_project_clears_stale_records_left_by_previous_deletion(monkeypatch, tmp_path) -> None:
    """旧删除残留（DB 行 + 内存任务）时，创建同名项目必须从空开始。"""
    from story import routes_project

    session_factory = _isolated_user_db(monkeypatch)
    stale_entry = _register_task(user_id="7", project_name="demo", agent_id="agent_director", status="completed", finished=True)

    monkeypatch.setattr("core.utils.USERDATA_ROOT", str(tmp_path))
    with session_factory() as session:
        session.add(ChatMessage(user_id=7, project_name="demo", agent_id="agent_director", context_key="global", role="user", content="残留聊天"))
        session.add(ProjectVersion(id="v-stale", user_id=7, project_name="demo", version_name="v1", snapshot_path="stale.db"))
        session.add(Share(id="s-stale", user_id=7, project_name="demo", title="残留分享", snapshot_path="stale.db"))
        session.commit()

    try:
        result = asyncio.run(
            routes_project.create_project(routes_project.ProjectCreate(projectName="demo"), {"user_id": 7})
        )

        assert result["success"] is True
        with session_factory() as session:
            assert session.execute(select(ChatMessage).where(ChatMessage.user_id == 7)).all() == []
            assert session.execute(select(ProjectVersion).where(ProjectVersion.user_id == 7)).all() == []
            assert session.execute(select(Share).where(Share.user_id == 7)).all() == []
        assert chat_task.list_recent_tasks("7", "demo") == []
        assert stale_entry.task_key not in chat_task._active_chat_tasks
    finally:
        chat_task._active_chat_tasks.pop(stale_entry.task_key, None)
