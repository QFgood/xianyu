"""Create task configuration without generating or reading template files."""
from src.domain.models.task import TaskCreate, TaskGenerateRequest

def build_task_create(req: TaskGenerateRequest) -> TaskCreate:
    return TaskCreate(
        task_name=req.task_name,
        monitor_mode=req.monitor_mode,
        product_selection=req.product_selection,
        enabled=True,
        keyword=req.keyword,
        description=req.description or "",
        analyze_images=req.analyze_images,
        max_pages=req.max_pages,
        personal_only=req.personal_only,
        min_price=req.min_price,
        max_price=req.max_price,
        cron=req.cron,
        ai_prompt_base_file="",
        ai_prompt_criteria_file="",
        account_state_file=req.account_state_file,
        account_strategy=req.account_strategy,
        free_shipping=req.free_shipping,
        new_publish_option=req.new_publish_option,
        region=req.region,
        decision_mode=req.decision_mode or "ai",
        keyword_rules=req.keyword_rules,
        prefilter=req.prefilter,
    )

