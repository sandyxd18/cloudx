import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Alert from '@cloudscape-design/components/alert'
import Container from '@cloudscape-design/components/container'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Multiselect from '@cloudscape-design/components/multiselect'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Textarea from '@cloudscape-design/components/textarea'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import FormPage from '../components/FormPage.jsx'

const DEFAULT_ROOT_GB = 8

const fmtRam = (mb) => (mb >= 1024 ? `${(mb / 1024).toFixed(mb % 1024 ? 1 : 0)} GB` : `${mb} MB`)

export default function ServerLaunchPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [form, setForm] = useState({ name: '', flavor: null, image: null, keypair: null, network: null })
  const [count, setCount]           = useState('1')
  const [rootSizeGb, setRootSizeGb] = useState(String(DEFAULT_ROOT_GB))
  const [volumeType, setVolumeType] = useState(null)
  const [selectedSecGroups, setSelectedSecGroups] = useState([])
  const [selectedServerGroup, setSelectedServerGroup] = useState(null)
  const [userData, setUserData]     = useState('')
  const [flavors, setFlavors]       = useState([])
  const [images, setImages]         = useState([])
  const [keypairs, setKeypairs]     = useState([])
  const [networks, setNetworks]     = useState([])
  const [volTypes, setVolTypes]     = useState([])
  const [secGroups, setSecGroups]   = useState([])
  const [serverGroups, setServerGroups] = useState([])
  const [loading, setLoading]       = useState(false)
  const [error, setError]           = useState('')

  useEffect(() => {
    Promise.allSettled([
      api.getFlavors(),
      api.getImages(),
      api.getKeypairs(),
      api.getNetworks(),
      api.getVolumeTypes(),
      api.getSecurityGroups(),
      api.getServerGroups(),
    ]).then(([f, im, k, n, vt, sg, srvg]) => {
      if (f.status === 'fulfilled')  setFlavors(f.value?.items || [])
      if (im.status === 'fulfilled') setImages((im.value?.items || []).filter(i => i.status === 'ACTIVE'))
      if (k.status === 'fulfilled')  setKeypairs(k.value?.items || [])
      if (n.status === 'fulfilled') {
        const allNets = n.value?.items || []
        const tenantNets = allNets.filter(net => !net.external && net['router:external'] !== 'External')
        setNetworks(tenantNets)
        if (tenantNets.length) {
          setForm(f => ({ ...f, network: { value: tenantNets[0].name, label: tenantNets[0].name } }))
        }
      }
      if (vt.status === 'fulfilled') {
        const types = vt.value?.items || []
        setVolTypes(types)
        if (types.length) setVolumeType({ value: types[0].name, label: types[0].name })
      }
      if (sg.status === 'fulfilled') {
        const sgs = sg.value?.items || []
        setSecGroups(sgs)
        const defSg = sgs.find(s => s.name === 'default')
        if (defSg) setSelectedSecGroups([{ value: defSg.name, label: defSg.name }])
      }
      if (srvg.status === 'fulfilled') {
        setServerGroups(srvg.value?.items || [])
      }
    })
  }, [])

  const back = () => navigate('/servers')

  const submit = async () => {
    if (!form.name || !form.flavor || !form.image || !form.network) {
      setError('Name, flavor, image, and network are required.')
      return
    }
    const numInstances = parseInt(count, 10) || 1
    if (numInstances < 1) {
      setError('Instance count must be at least 1.')
      return
    }
    if (rootSizeGb !== '' && Number(rootSizeGb) < 1) {
      setError('Root volume size must be at least 1 GiB.')
      return
    }
    setLoading(true); setError('')
    try {
      const payloadBase = {
        flavor: form.flavor.value,
        image: form.image.value,
        network: form.network.value,
        key_name: form.keypair?.value || undefined,
        volume_size: rootSizeGb !== '' ? Number(rootSizeGb) : DEFAULT_ROOT_GB,
        volume_type: volumeType?.value || undefined,
        security_groups: selectedSecGroups.map(s => s.value),
        server_group: selectedServerGroup?.value || undefined,
        user_data: userData.trim() || undefined,
      }

      if (numInstances === 1) {
        await api.createServer({ ...payloadBase, name: form.name.trim() })
      } else {
        for (let i = 1; i <= numInstances; i++) {
          await api.createServer({ ...payloadBase, name: `${form.name.trim()}-${i}` })
        }
      }

      toast(`${numInstances} instance(s) launch started`, 'success')
      back()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <FormPage
      title="Launch instance"
      description="Virtual machines backed by libvirt/KVM with boot-from-volume root disks."
      error={error}
      loading={loading}
      submitText="Launch instance"
      submitTestId="server-create-submit"
      onCancel={back}
      onSubmit={submit}
    >
      <Container header={<Header variant="h2">Name and count</Header>}>
        <SpaceBetween size="l">
          <FormField label="Instance name" description="Base name for the instance(s).">
            <Input
              value={form.name}
              onChange={({ detail }) => setForm(f => ({ ...f, name: detail.value }))}
              placeholder="my-instance"
              controlId="server-name"
              autoFocus
            />
          </FormField>
          <FormField label="Instance count" description="Number of instances to create (default 1).">
            <Input
              value={count}
              onChange={({ detail }) => setCount(detail.value)}
              type="number"
              inputMode="numeric"
              placeholder="1"
              controlId="server-count"
            />
          </FormField>
        </SpaceBetween>
      </Container>

      <Container header={<Header variant="h2">Application and OS image</Header>}>
        <FormField label="Image" description="Boot image (root volume is created from it).">
          <Select
            selectedOption={form.image}
            onChange={({ detail }) => setForm(f => ({ ...f, image: detail.selectedOption }))}
            options={images.map(im => ({ value: im.name, label: im.name, description: im.disk_format }))}
            placeholder="Search and choose an image"
            empty="No active images"
            filteringType="auto"
            controlId="server-image"
          />
        </FormField>
      </Container>

      <Container header={<Header variant="h2">Instance type</Header>}>
        <FormField label="Flavor" description="Compute and memory profile — storage size is configured separately below.">
          <Select
            selectedOption={form.flavor}
            onChange={({ detail }) => setForm(f => ({ ...f, flavor: detail.selectedOption }))}
            options={flavors.map(fl => ({
              value: fl.name,
              label: fl.name,
              description: `${fl.vcpus} vCPU · ${fmtRam(fl.ram_mb)}`,
            }))}
            placeholder="Search and choose a flavor"
            empty="No flavors defined"
            filteringType="auto"
            controlId="server-flavor"
          />
        </FormField>
      </Container>

      <Container header={<Header variant="h2">Key pair & Groups</Header>}>
        <SpaceBetween size="l">
          <FormField label="Key pair" description="Optional — injected via cloud-init for SSH access.">
            <Select
              selectedOption={form.keypair}
              onChange={({ detail }) => setForm(f => ({ ...f, keypair: detail.selectedOption }))}
              options={keypairs.map(k => ({ value: k.name, label: k.name }))}
              placeholder="Search for a key pair"
              empty="No key pairs"
              filteringType="auto"
              controlId="server-keypair"
            />
          </FormField>

          <FormField label="Security groups" description="Traffic control rules for instance network ports.">
            <Multiselect
              selectedOptions={selectedSecGroups}
              onChange={({ detail }) => setSelectedSecGroups(detail.selectedOptions)}
              options={secGroups.map(s => ({ value: s.name, label: s.name, description: s.description || undefined }))}
              placeholder="Select security groups"
              empty="No security groups"
              controlId="server-secgroups"
            />
          </FormField>

          <FormField label="Server group" description="Optional — affinity or anti-affinity policy group.">
            <Select
              selectedOption={selectedServerGroup}
              onChange={({ detail }) => setSelectedServerGroup(detail.selectedOption)}
              options={serverGroups.map(sg => ({ value: sg.name, label: sg.name, description: sg.policy }))}
              placeholder="Select a server group (optional)"
              empty="No server groups"
              filteringType="auto"
              controlId="server-servergroup"
            />
          </FormField>
        </SpaceBetween>
      </Container>

      <Container header={<Header variant="h2">Network settings</Header>}>
        <FormField label="Network">
          <Select
            selectedOption={form.network}
            onChange={({ detail }) => setForm(f => ({ ...f, network: detail.selectedOption }))}
            options={networks.map(n => ({ value: n.name, label: n.name }))}
            placeholder="Search and choose a network"
            empty="No networks"
            filteringType="auto"
            controlId="server-network"
          />
        </FormField>
      </Container>

      <Container header={<Header variant="h2" description="EC2-style block device mapping — the size is fully user-defined">Storage</Header>}>
        <SpaceBetween size="l">
          <FormField
            label="Root volume size (GiB)"
            description={`Default ${DEFAULT_ROOT_GB} GiB — freely customizable (minimum 1 GiB).`}
          >
            <Input
              value={rootSizeGb}
              onChange={({ detail }) => setRootSizeGb(detail.value)}
              type="number"
              inputMode="numeric"
              placeholder={String(DEFAULT_ROOT_GB)}
              controlId="server-root-size"
            />
          </FormField>

          {volTypes.length === 0 ? (
            <Alert type="warning" header="No volume types configured">
              An administrator must create at least one volume type before instances can be launched.
            </Alert>
          ) : (
            <FormField label="Volume type" description="Performance profile that also selects the storage backend.">
              <Select
                selectedOption={volumeType}
                onChange={({ detail }) => setVolumeType(detail.selectedOption)}
                options={volTypes.map(t => ({
                  value: t.name,
                  label: t.name,
                  description: `${t.disk_type} · ${String(t.backend || '').toUpperCase()}`,
                }))}
                filteringType="auto"
                controlId="server-vol-type"
              />
            </FormField>
          )}
        </SpaceBetween>
      </Container>

      <Container header={<Header variant="h2">Advanced options (User script)</Header>}>
        <FormField label="User script (user_data)" description="Custom shell script or cloud-config passed to cloud-init during instance initialization.">
          <Textarea
            value={userData}
            onChange={({ detail }) => setUserData(detail.value)}
            placeholder="#!/bin/bash&#10;echo 'Hello World' > /tmp/hello.txt"
            rows={5}
            controlId="server-user-data"
          />
        </FormField>
      </Container>
    </FormPage>
  )
}
