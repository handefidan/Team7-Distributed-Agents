import pytest
from backend.master_agent import decompose_requirement

def test_decomposition_schema():
    sample_req = "Develop an online library system with inventory and borrow features."
    res = decompose_requirement(sample_req)
    
    assert "project_title" in res
    assert "tasks" in res
    assert len(res["tasks"]) > 0
    
    first_task = res["tasks"][0]
    assert "task_id" in first_task
    assert "category" in first_task
    assert "dependencies" in first_task
    assert isinstance(first_task["dependencies"], list)
