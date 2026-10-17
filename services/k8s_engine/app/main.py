from shared.config import load_service_config
load_service_config("k8s-engine-api")
import os
import secrets
import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Integer, DateTime, ForeignKey, Boolean, select
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from shared.auth_middleware import CurrentUser, InternalAuth
from shared.schema import require_schema

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/cloudx_k8s_engine")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class ClusterTemplate(Base):
    __tablename__ = "k8s_cluster_templates"
    id = Column(String, primary_key=True, default=lambda: f"tpl-{uuid_hex(12)}")
    name = Column(String, nullable=False)
    k8s_version = Column(String, default="v1.30.2+k3s1")
    master_flavor = Column(String, default="m1.medium")
    worker_flavor = Column(String, default="m1.small")
    image_id = Column(String, nullable=False)
    network_driver = Column(String, default="ovn")
    volume_driver = Column(String, default="cloudx-csi")
    project_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

def uuid_hex(n: int) -> str:
    import uuid
    return uuid.uuid4().hex[:n]

class K8sCluster(Base):
    __tablename__ = "k8s_clusters"
    id = Column(String, primary_key=True, default=lambda: f"cls-{uuid_hex(12)}")
    name = Column(String, nullable=False)
    template_id = Column(String, ForeignKey("k8s_cluster_templates.id"), nullable=False)
    master_count = Column(Integer, default=1)
    worker_count = Column(Integer, default=2)
    network_id = Column(String, nullable=False)
    status = Column(String, default="CREATING")
    project_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    join_token = Column(String)
    admin_token = Column(String)
    status_reason = Column(String)
    master_ip = Column(String)
    ready_at = Column(DateTime)

class K8sNode(Base):
    __tablename__ = "k8s_nodes"
    id = Column(String, primary_key=True, default=lambda: f"node-{uuid_hex(12)}")
    cluster_id = Column(String, ForeignKey("k8s_clusters.id"), nullable=False)
    server_id = Column(String, nullable=False)
    role = Column(String, nullable=False)
    ip = Column(String)
    ready = Column(Boolean, default=False)

app = FastAPI(title="CloudX Kubernetes Engine Service API", version="1.0.0")

def project_id(current: CurrentUser) -> str:
    return current["project_id"]

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.on_event("startup")
def startup():
    require_schema(engine, "k8s_engine")

try:
    from shared.metrics import setup_metrics
    setup_metrics(app, "k8s-engine-api")
except Exception:  # best-effort: metrics setup failure is non-fatal
    pass

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "k8s-engine-api"}

class TemplateCreate(BaseModel):
    name: str
    k8s_version: Optional[str] = "v1.30.2+k3s1"
    master_flavor: Optional[str] = "m1.medium"
    worker_flavor: Optional[str] = "m1.small"
    image_id: str
    network_driver: Optional[str] = "ovn"
    volume_driver: Optional[str] = "cloudx-csi"

class ClusterCreate(BaseModel):
    name: str
    template_id: str
    master_count: Optional[int] = 1
    worker_count: Optional[int] = 2
    network_id: str

class ClusterScale(BaseModel):
    worker_count: int

def cluster_out(cls: K8sCluster) -> dict:
    return {
        "id": cls.id,
        "name": cls.name,
        "template_id": cls.template_id,
        "master_count": cls.master_count,
        "worker_count": cls.worker_count,
        "network_id": cls.network_id,
        "status": cls.status,
        "project_id": cls.project_id,
        "created_at": cls.created_at.isoformat() if cls.created_at else None,
        "status_reason": cls.status_reason,
        "master_ip": cls.master_ip,
        "ready_at": cls.ready_at.isoformat() if cls.ready_at else None,
    }

@app.post("/cluster-templates", status_code=201)
def create_cluster_template(body: TemplateCreate, x_project_id: str = Depends(project_id), db: Session = Depends(get_db)):
    tpl = ClusterTemplate(
        name=body.name,
        k8s_version=body.k8s_version,
        master_flavor=body.master_flavor,
        worker_flavor=body.worker_flavor,
        image_id=body.image_id,
        network_driver=body.network_driver,
        volume_driver=body.volume_driver,
        project_id=x_project_id
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl

@app.get("/cluster-templates")
def list_cluster_templates(x_project_id: str = Depends(project_id), db: Session = Depends(get_db)):
    return db.scalars(select(ClusterTemplate).where(ClusterTemplate.project_id == x_project_id)).all()

@app.get("/cluster-templates/{tpl_id}")
def get_cluster_template(tpl_id: str, x_project_id: str = Depends(project_id), db: Session = Depends(get_db)):
    tpl = db.get(ClusterTemplate, tpl_id)
    if not tpl or tpl.project_id != x_project_id:
        raise HTTPException(status_code=404, detail="Template not found")
    return tpl

@app.delete("/cluster-templates/{tpl_id}", status_code=204)
def delete_cluster_template(tpl_id: str, x_project_id: str = Depends(project_id), db: Session = Depends(get_db)):
    tpl = db.get(ClusterTemplate, tpl_id)
    if not tpl or tpl.project_id != x_project_id:
        raise HTTPException(status_code=404, detail="Template not found")
    db.delete(tpl)
    db.commit()
    return None

@app.post("/clusters", status_code=201)
def create_cluster(body: ClusterCreate, x_project_id: str = Depends(project_id), db: Session = Depends(get_db)):
    tpl = db.get(ClusterTemplate, body.template_id)
    if not tpl:
        raise HTTPException(status_code=400, detail="Invalid template_id")
    cls = K8sCluster(
        name=body.name,
        template_id=body.template_id,
        master_count=body.master_count,
        worker_count=body.worker_count,
        network_id=body.network_id,
        status="CREATING",
        project_id=x_project_id,
        join_token=secrets.token_urlsafe(24),
        admin_token=secrets.token_urlsafe(24),
    )
    db.add(cls)
    db.commit()
    db.refresh(cls)
    return cluster_out(cls)

@app.get("/clusters")
def list_clusters(x_project_id: str = Depends(project_id), db: Session = Depends(get_db)):
    clusters = db.scalars(select(K8sCluster).where(K8sCluster.project_id == x_project_id)).all()
    return [cluster_out(c) for c in clusters]

@app.get("/clusters/{cls_id}")
def get_cluster(cls_id: str, x_project_id: str = Depends(project_id), db: Session = Depends(get_db)):
    cls = db.get(K8sCluster, cls_id)
    if not cls or cls.project_id != x_project_id:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return cluster_out(cls)

@app.post("/clusters/{cls_id}/scale")
def scale_cluster(cls_id: str, body: ClusterScale, x_project_id: str = Depends(project_id), db: Session = Depends(get_db)):
    cls = db.get(K8sCluster, cls_id)
    if not cls or cls.project_id != x_project_id:
        raise HTTPException(status_code=404, detail="Cluster not found")
    if cls.status != "ACTIVE":
        raise HTTPException(status_code=409, detail="Cluster must be ACTIVE to scale")
    cls.worker_count = body.worker_count
    cls.status = "SCALING"
    db.commit()
    db.refresh(cls)
    return cluster_out(cls)

@app.get("/clusters/{cls_id}/kubeconfig")
def get_cluster_kubeconfig(cls_id: str, x_project_id: str = Depends(project_id), db: Session = Depends(get_db)):
    cls = db.get(K8sCluster, cls_id)
    if not cls or cls.project_id != x_project_id:
        raise HTTPException(status_code=404, detail="Cluster not found")
    if cls.status not in ("ACTIVE", "SCALING"):
        raise HTTPException(status_code=409, detail="Cluster must be ACTIVE to get kubeconfig")

    master_ip, admin_token = cls.master_ip, cls.admin_token
    if not master_ip:
        raise HTTPException(status_code=409, detail="Cluster has no master address yet")
    yaml_kubeconfig = f"""apiVersion: v1
clusters:
- cluster:
    server: https://{master_ip}:6443
    insecure-skip-tls-verify: true
  name: {cls.name}
contexts:
- context:
    cluster: {cls.name}
    user: admin-{cls.name}
  name: {cls.name}-context
current-context: {cls.name}-context
kind: Config
users:
- name: admin-{cls.name}
  user:
    token: {admin_token}
"""
    return {"kubeconfig": yaml_kubeconfig}

@app.delete("/clusters/{cls_id}", status_code=202)
def delete_cluster(cls_id: str, x_project_id: str = Depends(project_id), db: Session = Depends(get_db)):
    cls = db.get(K8sCluster, cls_id)
    if not cls or cls.project_id != x_project_id:
        raise HTTPException(status_code=404, detail="Cluster not found")
    cls.status = "DELETING"
    db.commit()
    return None

# Internal Endpoints
@app.get("/internal/k8s-engine/clusters", dependencies=[InternalAuth])
def internal_list_clusters(db: Session = Depends(get_db)):
    clusters = db.scalars(select(K8sCluster)).all()
    result = []
    for c in clusters:
        nodes = db.scalars(select(K8sNode).where(K8sNode.cluster_id == c.id)).all()
        d = cluster_out(c)
        d["join_token"] = c.join_token
        d["admin_token"] = c.admin_token
        d["nodes"] = [{"id": n.id, "server_id": n.server_id, "role": n.role, "ip": n.ip, "ready": n.ready} for n in nodes]
        result.append(d)
    return result

class NodeCreateInternal(BaseModel):
    server_id: str
    role: str

@app.post("/internal/k8s-engine/clusters/{cls_id}/nodes", status_code=201, dependencies=[InternalAuth])
def internal_create_node(cls_id: str, body: NodeCreateInternal, db: Session = Depends(get_db)):
    cls = db.get(K8sCluster, cls_id)
    if not cls:
        raise HTTPException(status_code=404, detail="Cluster not found")
    node = K8sNode(cluster_id=cls_id, server_id=body.server_id, role=body.role, ready=False)
    db.add(node)
    db.commit()
    db.refresh(node)
    return {"id": node.id, "server_id": node.server_id, "role": node.role, "ready": node.ready}

@app.delete("/internal/k8s-engine/clusters/{cls_id}/nodes/{node_id}", status_code=204, dependencies=[InternalAuth])
def internal_delete_node(cls_id: str, node_id: str, db: Session = Depends(get_db)):
    node = db.get(K8sNode, node_id)
    if not node or node.cluster_id != cls_id:
        raise HTTPException(status_code=404, detail="Node not found")
    db.delete(node)
    db.commit()
    return None

class NodeReadyInternal(BaseModel):
    role: str
    ip: str
    node_name: Optional[str] = None

@app.post("/internal/k8s-engine/clusters/{cls_id}/ready", dependencies=[InternalAuth])
def internal_node_ready(cls_id: str, body: NodeReadyInternal, db: Session = Depends(get_db)):
    """Called by the VM itself, which only knows its cluster id, role and IP."""
    cls = db.get(K8sCluster, cls_id)
    if not cls:
        raise HTTPException(status_code=404, detail="Cluster not found")
    nodes = db.scalars(select(K8sNode).where(K8sNode.cluster_id == cls_id, K8sNode.role == body.role)).all()
    # ponytail: unknown IP claims the first unready node of the role; exact once the IP is stored
    node = next((n for n in nodes if n.ip == body.ip), None) or next((n for n in nodes if not n.ready and not n.ip), None)
    if not node:
        raise HTTPException(status_code=404, detail="No matching node")
    node.ready = True
    node.ip = body.ip
    if body.role == "master":
        cls.master_ip = body.ip
    db.commit()
    return {"status": "ok"}

class ClusterStatusInternal(BaseModel):
    status: Optional[str] = None
    status_reason: Optional[str] = None
    master_ip: Optional[str] = None

@app.post("/internal/k8s-engine/clusters/{cls_id}/status", dependencies=[InternalAuth])
def internal_update_status(cls_id: str, body: ClusterStatusInternal, db: Session = Depends(get_db)):
    cls = db.get(K8sCluster, cls_id)
    if not cls:
        raise HTTPException(status_code=404, detail="Cluster not found")
    if body.status:
        cls.status = body.status
    if body.status_reason is not None:
        cls.status_reason = body.status_reason
    if body.master_ip:
        cls.master_ip = body.master_ip
    if body.status == "ACTIVE" and not cls.ready_at:
        cls.ready_at = datetime.datetime.utcnow()
    db.commit()
    return {"status": "ok"}

@app.delete("/internal/k8s-engine/clusters/{cls_id}", status_code=204, dependencies=[InternalAuth])
def internal_delete_cluster(cls_id: str, db: Session = Depends(get_db)):
    cls = db.get(K8sCluster, cls_id)
    if not cls:
        raise HTTPException(status_code=404, detail="Cluster not found")
    for n in db.scalars(select(K8sNode).where(K8sNode.cluster_id == cls_id)).all():
        db.delete(n)
    db.flush()  # no relationship(): without this the cluster row can be deleted before its nodes (FK violation on Postgres)
    db.delete(cls)
    db.commit()
    return None
