import { useEffect, useState, useCallback } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Modal from '@cloudscape-design/components/modal'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import Tabs from '@cloudscape-design/components/tabs'
import TextFilter from '@cloudscape-design/components/text-filter'
import Textarea from '@cloudscape-design/components/textarea'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

export default function K8sEnginePage() {
  const toast = useToast()
  const [activeTab, setActiveTab] = useState('clusters')

  // Clusters state
  const [clusters, setClusters] = useState([])
  const [loadingClusters, setLoadingClusters] = useState(true)
  const [clusterFilter, setClusterFilter] = useState('')
  const [selectedClusters, setSelectedClusters] = useState([])
  const [showCreateCluster, setShowCreateCluster] = useState(false)
  const [showScaleModal, setShowScaleModal] = useState(null)
  const [scaleCount, setScaleCount] = useState('2')
  const [scaling, setScaling] = useState(false)
  const [deleteClusterModal, setDeleteClusterModal] = useState(false)
  const [deletingCluster, setDeletingCluster] = useState(false)

  // Kubeconfig modal state
  const [kubeconfigModal, setKubeconfigModal] = useState(null)
  const [kubeconfigText, setKubeconfigText] = useState('')
  const [loadingKubeconfig, setLoadingKubeconfig] = useState(false)

  // Templates state
  const [templates, setTemplates] = useState([])
  const [loadingTemplates, setLoadingTemplates] = useState(true)
  const [templateFilter, setTemplateFilter] = useState('')
  const [selectedTemplates, setSelectedTemplates] = useState([])
  const [showCreateTemplate, setShowCreateTemplate] = useState(false)
  const [deleteTemplateModal, setDeleteTemplateModal] = useState(false)
  const [deletingTemplate, setDeletingTemplate] = useState(false)

  // Auxiliary data for dropdowns
  const [images, setImages] = useState([])
  const [flavors, setFlavors] = useState([])
  const [keypairs, setKeypairs] = useState([])

  // Create Cluster form
  const [clusterForm, setClusterForm] = useState({
    name: '',
    template: null,
    master_count: '1',
    worker_count: '2',
    keypair: null,
  })
  const [creatingCluster, setCreatingCluster] = useState(false)
  const [clusterError, setClusterError] = useState('')

  // Create Template form
  const [tplForm, setTplForm] = useState({
    name: '',
    image: null,
    k8s_version: 'v1.30.2+k3s1',
    master_flavor: null,
    worker_flavor: null,
    network_driver: 'ovn',
    volume_driver: 'cloudx-csi',
  })
  const [creatingTemplate, setCreatingTemplate] = useState(false)
  const [tplError, setTplError] = useState('')

  const loadClusters = useCallback(() => {
    setLoadingClusters(true)
    api.getK8sClusters()
      .then(r => setClusters(r?.items || r?.clusters || (Array.isArray(r) ? r : [])))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoadingClusters(false))
  }, [toast])

  const loadTemplates = useCallback(() => {
    setLoadingTemplates(true)
    api.getK8sTemplates()
      .then(r => setTemplates(r?.items || r?.cluster_templates || (Array.isArray(r) ? r : [])))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoadingTemplates(false))
  }, [toast])

  const loadAux = useCallback(() => {
    Promise.allSettled([
      api.getImages(),
      api.getFlavors(),
      api.getKeypairs(),
    ]).then(([im, fl, kp]) => {
      if (im.status === 'fulfilled') setImages(im.value?.items || [])
      if (fl.status === 'fulfilled') setFlavors(fl.value?.items || [])
      if (kp.status === 'fulfilled') setKeypairs(kp.value?.items || [])
    })
  }, [])

  useEffect(() => {
    loadClusters()
    loadTemplates()
    loadAux()
  }, [loadClusters, loadTemplates, loadAux])

  const handleCreateCluster = async () => {
    if (!clusterForm.name.trim() || !clusterForm.template) {
      setClusterError('Name and Cluster Template are required.')
      return
    }
    setCreatingCluster(true); setClusterError('')
    try {
      await api.createK8sCluster({
        name: clusterForm.name.trim(),
        template_id: clusterForm.template.value,
        master_count: parseInt(clusterForm.master_count, 10) || 1,
        worker_count: parseInt(clusterForm.worker_count, 10) || 2,
        keypair: clusterForm.keypair?.value || undefined,
      })
      toast('Kubernetes cluster creation started', 'success')
      setShowCreateCluster(false)
      setClusterForm({ name: '', template: null, master_count: '1', worker_count: '2', keypair: null })
      loadClusters()
    } catch (e) {
      setClusterError(e.message)
    } finally {
      setCreatingCluster(false)
    }
  }

  const handleCreateTemplate = async () => {
    if (!tplForm.name.trim() || !tplForm.image || !tplForm.master_flavor || !tplForm.worker_flavor) {
      setTplError('Name, OS Image, Master Flavor, and Worker Flavor are required.')
      return
    }
    setCreatingTemplate(true); setTplError('')
    try {
      await api.createK8sTemplate({
        name: tplForm.name.trim(),
        image_id: tplForm.image.value,
        k8s_version: tplForm.k8s_version.trim() || 'v1.30.2+k3s1',
        master_flavor: tplForm.master_flavor.value,
        worker_flavor: tplForm.worker_flavor.value,
        network_driver: tplForm.network_driver.trim() || 'ovn',
        volume_driver: tplForm.volume_driver.trim() || 'cloudx-csi',
      })
      toast('Cluster template created successfully', 'success')
      setShowCreateTemplate(false)
      setTplForm({
        name: '',
        image: null,
        k8s_version: 'v1.30.2+k3s1',
        master_flavor: null,
        worker_flavor: null,
        network_driver: 'ovn',
        volume_driver: 'cloudx-csi',
      })
      loadTemplates()
    } catch (e) {
      setTplError(e.message)
    } finally {
      setCreatingTemplate(false)
    }
  }

  const handleScaleCluster = async () => {
    if (!showScaleModal) return
    const count = parseInt(scaleCount, 10)
    if (isNaN(count) || count < 0) {
      toast('Worker count must be 0 or greater', 'error')
      return
    }
    setScaling(true)
    try {
      await api.scaleK8sCluster(showScaleModal.id, { worker_count: count })
      toast(`Cluster ${showScaleModal.name} scaling updated`, 'success')
      setShowScaleModal(null)
      loadClusters()
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setScaling(false)
    }
  }

  const handleDeleteClusters = async () => {
    if (!selectedClusters.length) return
    setDeletingCluster(true)
    for (const c of selectedClusters) {
      try { await api.deleteK8sCluster(c.id) } catch (e) { toast(e.message, 'error') }
    }
    setDeletingCluster(false)
    setDeleteClusterModal(false)
    setSelectedClusters([])
    toast(`${selectedClusters.length} cluster(s) delete requested`, 'success')
    loadClusters()
  }

  const handleDeleteTemplates = async () => {
    if (!selectedTemplates.length) return
    setDeletingTemplate(true)
    for (const t of selectedTemplates) {
      try { await api.deleteK8sTemplate(t.id) } catch (e) { toast(e.message, 'error') }
    }
    setDeletingTemplate(false)
    setDeleteTemplateModal(false)
    setSelectedTemplates([])
    toast(`${selectedTemplates.length} template(s) deleted`, 'success')
    loadTemplates()
  }

  const handleDownloadKubeconfig = async (cluster) => {
    setKubeconfigModal(cluster)
    setLoadingKubeconfig(true)
    try {
      const res = await api.getKubeconfig(cluster.id)
      const conf = res?.kubeconfig || (typeof res === 'string' ? res : JSON.stringify(res, null, 2))
      setKubeconfigText(conf)
    } catch (e) {
      toast(`Failed to load kubeconfig: ${e.message}`, 'error')
      setKubeconfigModal(null)
    } finally {
      setLoadingKubeconfig(false)
    }
  }

  const triggerDownloadFile = () => {
    if (!kubeconfigText || !kubeconfigModal) return
    const blob = new Blob([kubeconfigText], { type: 'text/yaml' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `kubeconfig-${kubeconfigModal.name}.yaml`
    a.click()
    URL.revokeObjectURL(url)
  }

  const cq = clusterFilter.trim().toLowerCase()
  const filteredClusters = cq
    ? clusters.filter(c => c.name?.toLowerCase().includes(cq) || c.status?.toLowerCase().includes(cq))
    : clusters

  const tq = templateFilter.trim().toLowerCase()
  const filteredTemplates = tq
    ? templates.filter(t => t.name?.toLowerCase().includes(tq) || t.k8s_version?.toLowerCase().includes(tq))
    : templates

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Managed Kubernetes cluster provisioning, node scaling, and templates"
        >
          Kubernetes Engine
        </Header>
      }
    >
      <Tabs
        activeTabId={activeTab}
        onChange={({ detail }) => setActiveTab(detail.activeTabId)}
        tabs={[
          {
            id: 'clusters',
            label: 'Clusters',
            content: (
              <Table
                variant="full-page"
                stickyHeader
                selectionType="multi"
                selectedItems={selectedClusters}
                onSelectionChange={({ detail }) => setSelectedClusters(detail.selectedItems)}
                trackBy="id"
                header={
                  <Header
                    variant="h2"
                    counter={counterText(clusters.length, selectedClusters.length)}
                    actions={
                      <SpaceBetween direction="horizontal" size="xs">
                        <Button iconName="refresh" onClick={loadClusters} ariaLabel="Refresh clusters" />
                        <Button disabled={!selectedClusters.length} onClick={() => setDeleteClusterModal(true)}>
                          Delete
                        </Button>
                        <Button variant="primary" onClick={() => setShowCreateCluster(true)}>
                          Create cluster
                        </Button>
                      </SpaceBetween>
                    }
                  >
                    Kubernetes Clusters
                  </Header>
                }
                columnDefinitions={[
                  { id: 'name', header: 'Cluster Name', cell: item => <Box fontWeight="bold">{item.name}</Box>, sortingField: 'name' },
                  { id: 'status', header: 'Status', cell: item => <ResourceStatus status={item.status} /> },
                  { id: 'masters', header: 'Master Nodes', cell: item => item.master_count ?? 1 },
                  { id: 'workers', header: 'Worker Nodes', cell: item => item.worker_count ?? 0 },
                  { id: 'keypair', header: 'Key Pair', cell: item => item.keypair || '—' },
                  {
                    id: 'actions',
                    header: 'Actions',
                    cell: item => (
                      <SpaceBetween direction="horizontal" size="xs">
                        <Button
                          variant="inline-link"
                          onClick={() => {
                            setShowScaleModal(item)
                            setScaleCount(String(item.worker_count ?? 2))
                          }}
                        >
                          Scale
                        </Button>
                        <Button
                          variant="inline-link"
                          onClick={() => handleDownloadKubeconfig(item)}
                        >
                          Kubeconfig
                        </Button>
                      </SpaceBetween>
                    ),
                  },
                ]}
                items={filteredClusters}
                loading={loadingClusters}
                loadingText="Loading clusters"
                filter={
                  <TextFilter
                    filteringText={clusterFilter}
                    onChange={({ detail }) => setClusterFilter(detail.filteringText)}
                    filteringPlaceholder="Filter clusters"
                  />
                }
                empty={
                  clusterFilter ? (
                    <TableNoMatch onClear={() => setClusterFilter('')} />
                  ) : (
                    <TableEmpty title="No Kubernetes clusters" subtitle="Launch a managed Kubernetes cluster to get started." />
                  )
                }
              />
            ),
          },
          {
            id: 'templates',
            label: 'Cluster Templates',
            content: (
              <Table
                variant="full-page"
                stickyHeader
                selectionType="multi"
                selectedItems={selectedTemplates}
                onSelectionChange={({ detail }) => setSelectedTemplates(detail.selectedItems)}
                trackBy="id"
                header={
                  <Header
                    variant="h2"
                    counter={counterText(templates.length, selectedTemplates.length)}
                    actions={
                      <SpaceBetween direction="horizontal" size="xs">
                        <Button iconName="refresh" onClick={loadTemplates} ariaLabel="Refresh templates" />
                        <Button disabled={!selectedTemplates.length} onClick={() => setDeleteTemplateModal(true)}>
                          Delete
                        </Button>
                        <Button variant="primary" onClick={() => setShowCreateTemplate(true)}>
                          Create template
                        </Button>
                      </SpaceBetween>
                    }
                  >
                    Cluster Templates
                  </Header>
                }
                columnDefinitions={[
                  { id: 'name', header: 'Template Name', cell: item => <Box fontWeight="bold">{item.name}</Box>, sortingField: 'name' },
                  { id: 'version', header: 'K8s Version', cell: item => item.k8s_version },
                  { id: 'master_flavor', header: 'Master Flavor', cell: item => item.master_flavor },
                  { id: 'worker_flavor', header: 'Worker Flavor', cell: item => item.worker_flavor },
                  { id: 'net_driver', header: 'Network Driver', cell: item => item.network_driver || 'ovn' },
                  { id: 'vol_driver', header: 'Volume Driver', cell: item => item.volume_driver || 'cloudx-csi' },
                ]}
                items={filteredTemplates}
                loading={loadingTemplates}
                loadingText="Loading templates"
                filter={
                  <TextFilter
                    filteringText={templateFilter}
                    onChange={({ detail }) => setTemplateFilter(detail.filteringText)}
                    filteringPlaceholder="Filter templates"
                  />
                }
                empty={
                  templateFilter ? (
                    <TableNoMatch onClear={() => setTemplateFilter('')} />
                  ) : (
                    <TableEmpty title="No cluster templates" subtitle="Create a template defining version and flavors for K8s clusters." />
                  )
                }
              />
            ),
          },
        ]}
      />

      {/* Modal: Create Cluster */}
      {showCreateCluster && (
        <Modal
          visible
          onDismiss={() => setShowCreateCluster(false)}
          header="Create Kubernetes Cluster"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowCreateCluster(false)}>Cancel</Button>
                <Button variant="primary" loading={creatingCluster} onClick={handleCreateCluster}>
                  Create cluster
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {clusterError && <Box color="text-status-error">{clusterError}</Box>}
            <FormField label="Cluster Name">
              <Input
                value={clusterForm.name}
                onChange={({ detail }) => setClusterForm(f => ({ ...f, name: detail.value }))}
                placeholder="production-k8s"
                autoFocus
              />
            </FormField>
            <FormField label="Cluster Template">
              <Select
                selectedOption={clusterForm.template}
                onChange={({ detail }) => setClusterForm(f => ({ ...f, template: detail.selectedOption }))}
                options={templates.map(t => ({
                  value: t.id,
                  label: t.name,
                  description: `${t.k8s_version} · Master: ${t.master_flavor} · Worker: ${t.worker_flavor}`,
                }))}
                placeholder="Select a cluster template"
                empty="No cluster templates available"
              />
            </FormField>
            <FormField label="Master Node Count">
              <Input
                type="number"
                value={clusterForm.master_count}
                onChange={({ detail }) => setClusterForm(f => ({ ...f, master_count: detail.value }))}
              />
            </FormField>
            <FormField label="Worker Node Count">
              <Input
                type="number"
                value={clusterForm.worker_count}
                onChange={({ detail }) => setClusterForm(f => ({ ...f, worker_count: detail.value }))}
              />
            </FormField>
            <FormField label="Key Pair (Optional)">
              <Select
                selectedOption={clusterForm.keypair}
                onChange={({ detail }) => setClusterForm(f => ({ ...f, keypair: detail.selectedOption }))}
                options={keypairs.map(k => ({ value: k.name, label: k.name }))}
                placeholder="Select keypair for SSH node access"
                empty="No keypairs"
              />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}

      {/* Modal: Create Template */}
      {showCreateTemplate && (
        <Modal
          visible
          onDismiss={() => setShowCreateTemplate(false)}
          header="Create Cluster Template"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowCreateTemplate(false)}>Cancel</Button>
                <Button variant="primary" loading={creatingTemplate} onClick={handleCreateTemplate}>
                  Create template
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {tplError && <Box color="text-status-error">{tplError}</Box>}
            <FormField label="Template Name">
              <Input
                value={tplForm.name}
                onChange={({ detail }) => setTplForm(f => ({ ...f, name: detail.value }))}
                placeholder="standard-k3s-template"
                autoFocus
              />
            </FormField>
            <FormField label="Base Node Image">
              <Select
                selectedOption={tplForm.image}
                onChange={({ detail }) => setTplForm(f => ({ ...f, image: detail.selectedOption }))}
                options={images.map(im => ({ value: im.id, label: im.name }))}
                placeholder="Select VM base image"
                empty="No images"
              />
            </FormField>
            <FormField label="Kubernetes Version">
              <Input
                value={tplForm.k8s_version}
                onChange={({ detail }) => setTplForm(f => ({ ...f, k8s_version: detail.value }))}
                placeholder="v1.30.2+k3s1"
              />
            </FormField>
            <FormField label="Master Node Flavor">
              <Select
                selectedOption={tplForm.master_flavor}
                onChange={({ detail }) => setTplForm(f => ({ ...f, master_flavor: detail.selectedOption }))}
                options={flavors.map(fl => ({ value: fl.name, label: fl.name, description: `${fl.vcpus} vCPU · ${fl.ram_mb} MB RAM` }))}
                placeholder="Select master flavor"
              />
            </FormField>
            <FormField label="Worker Node Flavor">
              <Select
                selectedOption={tplForm.worker_flavor}
                onChange={({ detail }) => setTplForm(f => ({ ...f, worker_flavor: detail.selectedOption }))}
                options={flavors.map(fl => ({ value: fl.name, label: fl.name, description: `${fl.vcpus} vCPU · ${fl.ram_mb} MB RAM` }))}
                placeholder="Select worker flavor"
              />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}

      {/* Modal: Scale Cluster */}
      {showScaleModal && (
        <Modal
          visible
          onDismiss={() => setShowScaleModal(null)}
          header={`Scale Cluster: ${showScaleModal.name}`}
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowScaleModal(null)}>Cancel</Button>
                <Button variant="primary" loading={scaling} onClick={handleScaleCluster}>
                  Update scale
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            <FormField label="Worker Node Count" description="Scale worker nodes up or down">
              <Input
                type="number"
                value={scaleCount}
                onChange={({ detail }) => setScaleCount(detail.value)}
                autoFocus
              />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}

      {/* Modal: Kubeconfig */}
      {kubeconfigModal && (
        <Modal
          visible
          onDismiss={() => setKubeconfigModal(null)}
          header={`Kubeconfig — ${kubeconfigModal.name}`}
          size="large"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setKubeconfigModal(null)}>Close</Button>
                <Button variant="primary" iconName="download" onClick={triggerDownloadFile}>
                  Download Kubeconfig
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {loadingKubeconfig ? (
              <Box>Loading kubeconfig...</Box>
            ) : (
              <Textarea
                value={kubeconfigText}
                readOnly
                rows={16}
              />
            )}
          </SpaceBetween>
        </Modal>
      )}

      {/* Delete Modals */}
      {deleteClusterModal && (
        <DeleteModal
          visible
          itemCount={selectedClusters.length}
          itemName={selectedClusters.length === 1 ? selectedClusters[0].name : undefined}
          title="Delete Kubernetes Cluster"
          loading={deletingCluster}
          onDismiss={() => setDeleteClusterModal(false)}
          onConfirm={handleDeleteClusters}
        />
      )}

      {deleteTemplateModal && (
        <DeleteModal
          visible
          itemCount={selectedTemplates.length}
          itemName={selectedTemplates.length === 1 ? selectedTemplates[0].name : undefined}
          title="Delete Cluster Template"
          loading={deletingTemplate}
          onDismiss={() => setDeleteTemplateModal(false)}
          onConfirm={handleDeleteTemplates}
        />
      )}
    </ContentLayout>
  )
}
