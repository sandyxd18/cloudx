import os
from shared.config import load_service_config
load_service_config("k8s-engine-api")

import uuid
import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Integer, DateTime, ForeignKey, select
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from shared.auth_middleware import CurrentUser
from shared.schema import require_schema

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/cloudx_k8s_engine")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class ClusterTemplate(Base):
    __tablename__ = "k8s_cluster_templates"
    id = Column(String, primary_key=True, default=lambda: f"tpl-{uuid.uuid4().hex[:12]}")
    name = Column(String, nullable=False)
    k8s_version = Column(String, default="v1.30.2+k3s1")
    master_flavor = Column(String, default="m1.medium")
    worker_flavor = Column(String, default="m1.small")
    image_id = Column(String, nullable=False)
    network_driver = Column(String, default="ovn")
    volume_driver = Column(String, default="cloudx-csi")
    project_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class K8sCluster(Base):
    __tablename__ = "k8s_clusters"
    id = Column(String, primary_key=True, default=lambda: f"cls-{uuid.uuid4().hex[:12]}")
    name = Column(String, nullable=False)
    template_id = Column(String, ForeignKey("k8s_cluster_templates.id"), nullable=False)
    master_count = Column(Integer, default=1)
    worker_count = Column(Integer, default=2)
    network_id = Column(String, nullable=False)
    status = Column(String, default="ACTIVE")
    project_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

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
        status="ACTIVE",
        project_id=x_project_id
    )
    db.add(cls)
    db.commit()
    db.refresh(cls)
    return cls

@app.get("/clusters")
def list_clusters(x_project_id: str = Depends(project_id), db: Session = Depends(get_db)):
    return db.scalars(select(K8sCluster).where(K8sCluster.project_id == x_project_id)).all()

@app.get("/clusters/{cls_id}")
def get_cluster(cls_id: str, x_project_id: str = Depends(project_id), db: Session = Depends(get_db)):
    cls = db.get(K8sCluster, cls_id)
    if not cls or cls.project_id != x_project_id:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return cls

@app.post("/clusters/{cls_id}/scale")
def scale_cluster(cls_id: str, body: ClusterScale, x_project_id: str = Depends(project_id), db: Session = Depends(get_db)):
    cls = db.get(K8sCluster, cls_id)
    if not cls or cls.project_id != x_project_id:
        raise HTTPException(status_code=404, detail="Cluster not found")
    cls.worker_count = body.worker_count
    db.commit()
    db.refresh(cls)
    return cls

@app.get("/clusters/{cls_id}/kubeconfig")
def get_cluster_kubeconfig(cls_id: str, x_project_id: str = Depends(project_id), db: Session = Depends(get_db)):
    cls = db.get(K8sCluster, cls_id)
    if not cls or cls.project_id != x_project_id:
        raise HTTPException(status_code=404, detail="Cluster not found")
    
    yaml_kubeconfig = f"""apiVersion: v1
clusters:
- cluster:
    server: https://10.0.0.100:6443
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
    token: cloudx-k8s-dummy-token-{cls.id}
"""
    return {"kubeconfig": yaml_kubeconfig}

@app.delete("/clusters/{cls_id}", status_code=204)
def delete_cluster(cls_id: str, x_project_id: str = Depends(project_id), db: Session = Depends(get_db)):
    cls = db.get(K8sCluster, cls_id)
    if not cls or cls.project_id != x_project_id:
        raise HTTPException(status_code=404, detail="Cluster not found")
    db.delete(cls)
    db.commit()
    return None
