from fastapi import APIRouter, Depends

from ..auth import get_current_user
from ..schemas import UserOut

router = APIRouter(prefix="/me", tags=["users"])


@router.get("", response_model=UserOut)
async def read_me(current_user=Depends(get_current_user)):
    return current_user
