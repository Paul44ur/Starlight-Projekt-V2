"""Skills the AI learns (Step 9). A skill is a folder in ``skills/``:

    skills/backup-check/
        SKILL.md     front matter (name, description, status, version ...) + instructions
        example.md   optional: an example request and answer
        code/        optional: code that worked

New skills start as drafts. Approved skills (``starlight skills approve <name>``) are
added to the prompt automatically when a request matches them. Every change is committed
to a separate git history in ``data/skills.git``, so a bad skill can be rolled back.
"""

from starlight.skills.manager import SkillManager
from starlight.skills.store import Skill, SkillStore

__all__ = ["Skill", "SkillManager", "SkillStore"]
