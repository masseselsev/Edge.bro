from fastapi import APIRouter

# Initialize main nodes router
router = APIRouter()

# Include CRUD and Actions sub-routers
from routers.nodes_crud import router as crud_router, parse_ip_input, get_nodes, get_all_history, add_node, delete_node
from routers.nodes_actions import router as actions_router, apply_saved_license_task, checkin_restored_node
from routers.terminal import router as terminal_router

router.include_router(crud_router)
router.include_router(actions_router)
router.include_router(terminal_router)
