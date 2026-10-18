"""GBRL modelini dışa aktarma seçeneklerini inceler (canlıya taşıma planı için): kaydet/yükle, C dışa aktarımı, SHAP."""
import inspect

from gbrl.models.actor_critic import ActorCritic
from gbrl.learners import gbt_learner

print("learner modülü:", [n for n in dir(gbt_learner) if not n.startswith("_")][:30])
names = [n for n, _ in inspect.getmembers(ActorCritic, predicate=inspect.isfunction) if not n.startswith("_")]
print("ActorCritic:", names)
for n in names:
    if any(k in n for k in ("export", "save", "load", "shap", "tree", "plot")):
        f = getattr(ActorCritic, n)
        print("--", n, inspect.signature(f), "|", " ".join((f.__doc__ or "").split())[:240])
