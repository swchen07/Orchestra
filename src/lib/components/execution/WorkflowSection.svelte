<script lang="ts">
	export let title: string;
	export let description = '';
</script>

<section class="workflow-section" aria-label={title}>
	<div class="section-heading">
		<h3>{title}</h3>
		{#if description}<p>{description}</p>{/if}
	</div>
	<div class="section-fields"><slot /></div>
</section>

<style>
	.workflow-section {
		display: grid;
		grid-template-columns: 11rem minmax(0, 1fr);
		gap: 1.75rem;
		padding: 1.5rem 0;
		border-top: 1px solid rgb(128 128 128 / 15%);
	}
	h3 {
		font-size: 0.875rem;
		font-weight: 600;
	}
	p {
		margin-top: 0.375rem;
		font-size: 0.75rem;
		line-height: 1.6;
		color: rgb(128 128 128);
	}
	.section-fields {
		min-width: 0;
	}
	@media (max-width: 900px) {
		.workflow-section {
			grid-template-columns: minmax(0, 1fr);
			gap: 1rem;
		}
	}
</style>
