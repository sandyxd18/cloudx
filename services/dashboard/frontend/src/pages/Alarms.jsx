import { useState, useEffect, useCallback } from 'react'
import Alert from '@cloudscape-design/components/alert'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Modal from '@cloudscape-design/components/modal'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import StatusIndicator from '@cloudscape-design/components/status-indicator'
import Table from '@cloudscape-design/components/table'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { TableEmpty, counterText, fmtDate } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

const STATE = { ok: 'success', alarm: 'error', insufficient_data: 'pending' }
const opt = v => ({ value: v, label: v })
const EMPTY = { name: '', metric_type: 'cpu_util', instance_id: '', comparison: opt('>'), threshold: '80', period: '300', evaluation_periods: '1', statistic: opt('avg') }

export default function AlarmsPage() {
  const toast = useToast()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState([])
  const [history, setHistory] = useState([])
  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState(EMPTY)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [showDelete, setShowDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.getAlarms().then(d => setItems(Array.isArray(d) ? d : d?.items || []))
      .catch(e => toast(e.message || 'Failed to load alarms', 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const pick = sel => {
    setSelected(sel)
    setHistory([])
    if (sel.length === 1) api.getAlarmHistory(sel[0].id).then(setHistory).catch(() => setHistory([]))
  }

  const set = k => ({ detail }) => setForm(f => ({ ...f, [k]: detail.value }))

  const create = async () => {
    setSaving(true); setError('')
    try {
      await api.createAlarm({
        name: form.name, metric_type: form.metric_type, comparison: form.comparison.value,
        threshold: Number(form.threshold), period: Number(form.period),
        evaluation_periods: Number(form.evaluation_periods), statistic: form.statistic.value,
        ...(form.instance_id ? { instance_id: form.instance_id } : {}),
      })
      toast('Alarm created', 'success')
      setShowAdd(false); setForm(EMPTY); load()
    } catch (e) { setError(e.message) } finally { setSaving(false) }
  }

  const remove = async () => {
    setDeleting(true)
    for (const a of selected) {
      try { await api.deleteAlarm(a.id) } catch (e) { toast(e.message, 'error') }
    }
    setDeleting(false); setShowDelete(false); pick([]); load()
  }

  return (
    <ContentLayout header={<Header variant="h1" description="Threshold alarms on telemetry metrics">Alarms</Header>}>
      <SpaceBetween size="l">
        <Table
          variant="full-page"
          stickyHeader
          selectionType="multi"
          selectedItems={selected}
          onSelectionChange={({ detail }) => pick(detail.selectedItems)}
          trackBy="id"
          header={
            <Header
              variant="h2"
              counter={counterText(items.length, selected.length)}
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  <Button iconName="refresh" onClick={load} ariaLabel="Refresh alarms" />
                  <Button disabled={!selected.length} onClick={() => setShowDelete(true)}>Delete</Button>
                  <Button variant="primary" onClick={() => { setError(''); setShowAdd(true) }}>Create alarm</Button>
                </SpaceBetween>
              }
            >
              Alarms
            </Header>
          }
          columnDefinitions={[
            { id: 'name', header: 'Name', cell: a => <Box fontWeight="bold">{a.name}</Box> },
            { id: 'state', header: 'State', cell: a => <StatusIndicator type={STATE[a.state] || 'pending'}>{a.state || 'unknown'}</StatusIndicator> },
            { id: 'rule', header: 'Rule', cell: a => `${a.statistic}(${a.metric_type}) ${a.comparison} ${a.threshold}` },
            { id: 'period', header: 'Period', cell: a => `${a.period}s x ${a.evaluation_periods}` },
            { id: 'enabled', header: 'Enabled', cell: a => (a.enabled ? 'Yes' : 'No') },
          ]}
          items={items}
          loading={loading}
          loadingText="Loading alarms"
          empty={<TableEmpty title="No alarms" description="Create an alarm to watch a metric." />}
        />
        {selected.length === 1 && (
          <Table
            header={<Header variant="h2">History: {selected[0].name}</Header>}
            columnDefinitions={[
              { id: 'at', header: 'Time', cell: h => fmtDate(h.at) },
              { id: 'old', header: 'From', cell: h => h.old_state },
              { id: 'new', header: 'To', cell: h => h.new_state },
              { id: 'reason', header: 'Reason', cell: h => h.reason },
            ]}
            items={history}
            empty={<TableEmpty title="No state changes" />}
          />
        )}
      </SpaceBetween>

      {showAdd && (
        <Modal
          visible
          onDismiss={() => setShowAdd(false)}
          header="Create alarm"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowAdd(false)}>Cancel</Button>
                <Button variant="primary" loading={saving} disabled={!form.name} onClick={create}>Create</Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {error && <Alert type="error">{error}</Alert>}
            <FormField label="Name"><Input value={form.name} onChange={set('name')} /></FormField>
            <FormField label="Metric type"><Input value={form.metric_type} onChange={set('metric_type')} /></FormField>
            <FormField label="Instance ID" description="Optional. Empty means every instance of the project."><Input value={form.instance_id} onChange={set('instance_id')} /></FormField>
            <FormField label="Statistic">
              <Select selectedOption={form.statistic} onChange={({ detail }) => setForm(f => ({ ...f, statistic: detail.selectedOption }))} options={['avg', 'min', 'max', 'sum', 'count'].map(opt)} />
            </FormField>
            <FormField label="Comparison">
              <Select selectedOption={form.comparison} onChange={({ detail }) => setForm(f => ({ ...f, comparison: detail.selectedOption }))} options={['>', '>=', '<', '<='].map(opt)} />
            </FormField>
            <FormField label="Threshold"><Input type="number" value={form.threshold} onChange={set('threshold')} /></FormField>
            <FormField label="Period (seconds, 30-86400)"><Input type="number" value={form.period} onChange={set('period')} /></FormField>
            <FormField label="Evaluation periods"><Input type="number" value={form.evaluation_periods} onChange={set('evaluation_periods')} /></FormField>
          </SpaceBetween>
        </Modal>
      )}

      {showDelete && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete alarm"
          confirmButtonText="Delete"
          loading={deleting}
          onDismiss={() => setShowDelete(false)}
          onConfirm={remove}
        />
      )}
    </ContentLayout>
  )
}
